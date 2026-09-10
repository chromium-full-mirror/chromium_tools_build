# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""API for interacting with the re-client remote compiler."""

from __future__ import annotations

import contextlib
import io
import os
import re
import socket
import tarfile
import time

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

from google.protobuf import json_format, timestamp_pb2
from google.protobuf.message import DecodeError
from PB.recipe_modules.build.reclient import rbe_metrics_bq


_GS_BUCKET = 'chrome-goma-log'
# For a clobber build, the canonical RPL log size is ~3.5G, which is too
# expensive to be uploaded per build. The reduced format shrinks the file size
# by 90%. Furthermore, we also gzip compress the log file.
_REPROXY_LOG_FORMAT = 'reducedtext'

# For builds using the goma input processor, sometimes the deps cache file is
# too big for the default setting.  So just set the max file size permitted to
# be large enough.
_DEPS_CACHE_MAX_MB = {
  'linux': '256',
  'mac': '512',
  'win': '256',
}

_VALID_ENV_PREFIX_LIST = ['RBE_', 'GLOG_', 'GOMA_']


def make_test_rbe_stats_pb():
  stats = rbe_metrics_bq.RbeMetricsBq().stats

  p1 = stats.proxy_info.add()
  p1.flags['foo'] = 'bar'
  p1.flags['abc'] = 'def'
  p1.metrics['some'].bool_value = 1
  p1.metrics['other'].int64_value = 7
  p1.bq_stats["failed_bq_uploads"] = 1
  p1.bq_stats["success_bq_uploads"] = 42003

  s1 = p1.stats.add()
  s1.name = "CPU"

  s2 = p1.stats.add()
  s2.name = "MEM"

  p2 = stats.proxy_info.add()
  p2.metrics['test'].double_value = 1.1
  getattr(p2.event_times['foo'], 'from').CopyFrom(
    timestamp_pb2.Timestamp(seconds=1658269605)
  )
  p2.event_times['foo'].to.CopyFrom(timestamp_pb2.Timestamp(seconds=1658369605))
  return stats


def proxy_info_to_bq(proxy_info):
  proxy_info_bq = {}
  if proxy_info.event_times:
    proxy_info_bq['event_times'] = [
      {
        'key': evt,
        'value': json_format.MessageToDict(
          message=proxy_info.event_times[evt], preserving_proto_field_name=True
        ),
      }
      for evt in sorted(proxy_info.event_times)
    ]
  if proxy_info.flags:
    proxy_info_bq['flags'] = [
      {'key': flag, 'value': proxy_info.flags[flag]}
      for flag in sorted(proxy_info.flags)
    ]
  if proxy_info.bq_stats:
    proxy_info_bq['bq_stats'] = [
      {'key': key, 'value': proxy_info.bq_stats[key]}
      for key in sorted(proxy_info.bq_stats)
    ]
  if proxy_info.stats:
    proxy_info_bq['stats'] = [
      json_format.MessageToDict(message=stat, preserving_proto_field_name=True)
      for stat in proxy_info.stats
    ]
  if proxy_info.metrics:
    proxy_info_bq['metrics'] = [
      {
        'key': metric,
        'value': json_format.MessageToDict(
          message=proxy_info.metrics[metric], preserving_proto_field_name=True
        ),
      }
      for metric in sorted(proxy_info.metrics)
    ]
  return proxy_info_bq


class MalformedREClientFlag(Exception):
  def __init__(self, flag):
    full_message = 'Flag "{}" doesn\'t start with "RBE_"'.format(flag)
    super().__init__(full_message)


class BuildResultReceiver:
  def __init__(self):
    self.build_exit_status = -1


class FilenameMaker:
  """A helper to make filenames with a fixed (unique) suffix"""

  def __init__(self, timestamp, uuid):
    self._timestamp = timestamp
    self._gzip_suffix = '.%s.%s' % (timestamp.strftime('%Y%m%d-%H%M%S'), uuid)

  def make(self, prefix):
    return prefix + self._gzip_suffix

  def make_gz(self, prefix):
    return self.make(prefix) + '.gz'

  def make_tgz(self, prefix):
    return self.make(prefix) + '.tar.gz'

  @property
  def timestamp(self):
    return self._timestamp

  @property
  def timestamp_date(self):
    return self.timestamp.date().strftime('%Y/%m/%d')


class ReclientApi(recipe_api.RecipeApi):
  """A module for interacting with re-client."""

  def __init__(self, props, **kwargs):
    super().__init__(**kwargs)

    self._props = props
    self._instance = None
    self._metrics_project = None
    DEFAULT_SERVICE = 'remotebuildexecution.googleapis.com:443'
    self._service = props.service or DEFAULT_SERVICE
    self._jobs = props.jobs or None
    self._base_rewrapper_env = None
    self._cache_silo = props.cache_silo or None
    self._mismatch = None
    self._bootstrap_env = None
    self._scandeps_server = props.scandeps_server
    self._disable_bq_upload = props.disable_bq_upload
    self._credentials_helper = None
    self._credentials_helper_args = None

    if self._test_data.enabled:
      self._hostname = 'fakevm999-m9'
    else:  # pragma: no cover
      # TODO: find a recipe way to get hostname
      self._hostname = socket.gethostname()

  @property
  def instance(self):
    if self._instance:
      return self._instance

    self._instance = self._props.instance

    if self._instance and '/' not in self._instance:
      # Set full instance name if only project ID is given.
      self._instance = 'projects/%s/instances/default_instance' % self._instance

    return self._instance

  @property
  def rbe_project(self):
    return re.match('projects/(.+)/instances/.+', self.instance).group(1)

  @property
  def metrics_project(self):
    if self._metrics_project:
      return self._metrics_project

    self._metrics_project = self._props.metrics_project
    return self._metrics_project

  @property
  def jobs(self):
    """Returns number of jobs for parallel build using reclient."""
    if self._jobs is None:
      # Set lower value for Windows to prevent Access is denied error.
      # ref: http://b/281621756
      min_jobs = 160 if self.m.platform.is_win else 200
      self._jobs = min(20 * self.m.platform.cpu_count, min_jobs)
    if self._ensure_verified:
      self._jobs = self.m.platform.cpu_count
    return self._jobs

  @property
  def cache_silo(self):
    return self._cache_silo

  @cache_silo.setter
  def cache_silo(self, val):
    self._cache_silo = val

  def _rewrapper_env(
    self,
    buildtools_dir: Path,
    reclient_log_dir: Path,
    exec_strategy: str | None,
    invocation_id: str | None,
  ):
    # While this verification would better be placed at __init__, the test
    # framework 1) doesn't check for exceptions thrown during object creation,
    # 2) requires 100% code coverage. Thus we have to move any exception
    # throwing code -- such as checking validity of props inputs -- outside
    # __init__.
    if self._base_rewrapper_env is None:
      self._base_rewrapper_env = self._verify_reclient_flags(
        self._props.rewrapper_env
      )

    rewrapper_env = dict(self._base_rewrapper_env)
    rewrapper_env["RBE_invocation_id"] = invocation_id
    # This is the same as siso's job_id, which is the Buildbucket ID in the LUCI
    # webpage: https://ci.chromium.org/ui/b/$RBE_correlated_invocations_id.
    # Note that a buildbucket job may build multiple times under the same
    # Buildbucket ID.
    rewrapper_env["RBE_correlated_invocations_id"] = self.m.buildbucket.build.id

    if exec_strategy is not None:
      rewrapper_env['RBE_exec_strategy'] = exec_strategy
    else:
      # TODO: b/299613159 Validate if racing can be used in CQ
      # Once performance has been validated either remove this comment or enable
      # racing
      rewrapper_env.setdefault('RBE_exec_strategy', 'remote_local_fallback')

    # The log directory will be different each time reclient is invoked, so this
    # needs to be redone each time
    if 'RBE_server_address' not in rewrapper_env:
      rewrapper_env['RBE_server_address'] = self.server_address
      rewrapper_env['RBE_log_dir'] = reclient_log_dir

    # The reclient version needs to be checked each time because if a different
    # revision was checked out then the reclient version could be different
    rewrapper_env['RBE_canonicalize_working_dir'] = 'true'
    if self.m.platform.is_win:
      reclient_version = self._get_reclient_version_from_bootstrap(
        buildtools_dir
      )
      if reclient_version["MAJOR"] <= 0 and reclient_version["MINOR"] < 109:
        rewrapper_env['RBE_canonicalize_working_dir'] = 'false'

    return rewrapper_env

  @property
  def bootstrap_env(self):
    # While this verification would better be placed at __init__, the test
    # framework 1) doesn't check for exceptions thrown during object creation,
    # 2) requires 100% code coverage. Thus we have to move any exception
    # throwing code -- such as checking validity of props inputs -- outside
    # __init__.
    if self._bootstrap_env is None:
      self._bootstrap_env = self._verify_reclient_flags(
        self._props.bootstrap_env
      )
    return self._bootstrap_env

  def _get_reclient_version_from_bootstrap(self, buildtools_dir):
    """
    Calls bootstrap --version to get the current version of reclient.

    The dictionary will map the name of the portion of the version to its
    numeric value e.g.
    { 'MAJOR'": 0, 'MINOR': 100, 'PATCH': 1, 'HASH': 'abcdefgh'}
    """

    with self.m.step.nest('get reclient version') as version_step:
      result = self.m.step(
        'call bootstrap --version',
        [self._bootstrap_bin_path(buildtools_dir), "--version"],
        infra_step=True,
        stdout=self.m.raw_io.output_text(),
        step_test_data=(
          lambda: self.m.raw_io.test_api.stream_output_text(
            'Version: 0.110.0.aaaaaaaa'
          )
        ),
      ).stdout
      parts = result[len("Version: ") :].strip().split(".")
      version_dict = {
        'MAJOR': int(parts[0]),
        'MINOR': int(parts[1]),
        'PATCH': int(parts[2]),
        'HASH': parts[3],
      }
      version_step.logs['reclient_version'] = self.m.json.dumps(
        version_dict, indent=2
      )
      return version_dict

  def _bootstrap_bin_path(self, buildtools_dir: Path):
    return self._get_reclient_exe_path(buildtools_dir, 'bootstrap')

  def _rpl2cloudtrace_bin_path(self, buildtools_dir: Path):
    return self._get_reclient_exe_path(buildtools_dir, 'rpl2cloudtrace')

  def _scandeps_server_bin_path(self, buildtools_dir: Path):
    return self._get_reclient_exe_path(buildtools_dir, 'scandeps_server')

  @property
  def _ensure_verified(self):
    return self._props.ensure_verified

  @property
  def credentials_helper(self):
    return self._credentials_helper

  @credentials_helper.setter
  def credentials_helper(self, val):
    self._credentials_helper = val

  @property
  def credentials_helper_args(self):
    return self._credentials_helper_args

  @credentials_helper_args.setter
  def credentials_helper_args(self, val):
    self._credentials_helper_args = val

  def _get_platform_exe_name(self, exe_name):
    if self.m.platform.is_win:
      exe_name += '.exe'
    return exe_name

  def _get_reclient_exe_path(self, buildtools_dir: Path, exe_name):
    exe_name = self._get_platform_exe_name(exe_name)
    # This depends on where the reclient CIPD is checked out in DEPS,
    # https://source.chromium.org/chromium/chromium/src/+/main:DEPS;l=452-461;drc=6b88cf228d9d27f49e89f7c4d9ffb582771daa48
    return self.m.path.join(str(buildtools_dir), 'reclient', exe_name)

  @property
  def server_address(self):
    if self.m.platform.is_win:
      return 'pipe://reproxy.pipe'
    # Shrink the size if the domain socket path length becomes a problem.
    return 'unix:///%s' % self._tmp_base_dir.joinpath('reproxy.sock')

  @property
  def _tmp_base_dir(self):
    return self.m.path.tmp_base_dir

  @property
  def base_cache_path_per_follower(self):
    return self.m.path.cache_dir.joinpath('builder').joinpath('reclient')

  @property
  def deps_cache_path(self):
    safe_buildername = re.sub(
      r'[^a-zA-Z0-9]', '_', self.m.buildbucket.builder_name
    )
    data_cache = self.base_cache_path_per_follower.joinpath('deps')
    return data_cache.joinpath(safe_buildername)

  @contextlib.contextmanager
  def process(
    self,
    ninja_step_name,
    ninja_command,
    source_dir: Path,
    *,
    buildtools_dir: Path | None = None,
    deps_cache_by_step=False,
    skip_log_upload=False,
    skip_ninjalog_upload=False,
    exec_strategy=None,
    bootstrap_extra_env: dict | None = None,
    invocation_id=None,
  ):
    """Do preparation and cleanup steps for running the ninja command.

    Args:
      ninja_step_name: Step name of the ninja build.
      ninja_command: Command used for build.
                     (e.g. ['ninja', '-C', 'out/Release'])
      source_dir: The path to the top-level repository.
      buildtools_dir: The path to the buildtools directory. If not provided,
        then the 'buildtools' subdirectory of `source_dir` will be used.
      skip_log_upload: When true skip log uploading including cloudtail.
      skip_ninjalog_upload: When true skip ninjalog uploading.
      bootstrap_extra_env: Additional env vars for reclient to be used by bootstrap.
      invocation_id: ID of the build invocation.
    """
    buildtools_dir = buildtools_dir or source_dir / 'buildtools'
    reclient_log_dir = self.m.path.mkdtemp('reclient_log')
    deps_cache_path = self.deps_cache_path
    if deps_cache_by_step:
      deps_cache_path = deps_cache_path / ninja_step_name
    with self.m.step.nest('preprocess for reclient'):
      self.m.file.listdir(
        'list reclient_cfgs dir',
        buildtools_dir / 'reclient_cfgs',
        recursive=True,
        test_data=[
          'reproxy.cfg',
          'chromium-browser-clang/rewrapper_windows.cfg',
        ],
      )
      self._make_reclient_cache_dir(deps_cache_path)
      self._list_reclient_cache_dir(deps_cache_path)

      if not skip_log_upload:
        # TODO: remove 'goma-logs' fallback
        cloudtail_project_id = self.metrics_project or 'goma-logs'
        self._start_cloudtail(
          cloudtail_project_id,
          reclient_log_dir,
          self._get_platform_exe_name('reproxy') + '.INFO',
        )
        self._start_cloudtail(
          cloudtail_project_id, reclient_log_dir, 'reproxy-gomaip.INFO'
        )
        self._start_cloudtail(
          cloudtail_project_id, reclient_log_dir, 'reproxy_outerr.log'
        )

      self._start_reproxy(
        buildtools_dir, reclient_log_dir, deps_cache_path, bootstrap_extra_env
      )
      # This will get the reclient version the first time it is run,
      # so get it here to ensure it is nested in 'preprocess for reclient'
      rewrapper_env = self._rewrapper_env(
        buildtools_dir, reclient_log_dir, exec_strategy, invocation_id
      )

    p = BuildResultReceiver()
    try:
      with self.m.context(env=rewrapper_env):
        yield p
    finally:
      if not self.m.runtime.in_global_shutdown:
        with self.m.step.nest('postprocess for reclient'):
          self._stop_reproxy(buildtools_dir, reclient_log_dir, deps_cache_path)
          if not skip_log_upload:
            self._stop_cloudtail(
              self._get_platform_exe_name('reproxy') + '.INFO'
            )
            self._stop_cloudtail('reproxy-gomaip.INFO')
            self._stop_cloudtail('reproxy_outerr.log')
            self._upload_rbe_metrics(reclient_log_dir)
            if self._props.publish_trace:
              self._upload_reclient_traces(buildtools_dir, reclient_log_dir)
            filename_maker = FilenameMaker(
              self.m.time.utcnow(), self.m.uuid.random()
            )
            if ninja_command and not skip_ninjalog_upload:
              self.m.ninjalog.upload(
                ninja_step_name,
                ninja_command,
                p.build_exit_status,
                invocation_id,
              )
            self._upload_rpl(reclient_log_dir, filename_maker)
            log_dir_files = self.m.file.listdir(
              'list reclient log directory',
              reclient_log_dir,
              test_data=[
                'reproxy.INFO',
                'rewrapper.INFO',
                'reproxy.rpl',
                'reproxy_stderr.log',
                'reproxy-gomaip.LUCI-CHROMIUM-C.chrome-bot.log.ERROR.20220803-090904.9256',
              ],
            )
            self._upload_logs(log_dir_files, filename_maker)
            self._upload_crash_dumps(
              reclient_log_dir, log_dir_files, filename_maker
            )
          self._perform_reclient_health_check(
            reclient_log_dir, p.build_exit_status
          )
          if not skip_log_upload:
            self.m.file.rmtree('cleanup reclient log dir', reclient_log_dir)
          if self._ensure_verified:
            status = self.m.step.SUCCESS
            if self._mismatch:
              status = self.m.step.INFRA_FAILURE
            self.m.step.empty(
              'verification', status=status, step_text=self._mismatch
            )

  def download_reclient(self, gclient_solution):
    gclient_solution.custom_vars['download_reclient'] = 'True'

  def use_download_remoteexec_cfg_hook(self, gclient_solution):
    gclient_solution.custom_vars['rbe_instance'] = self.m.reclient.instance
    gclient_solution.custom_vars['download_remoteexec_cfg'] = 'True'

  def _make_reclient_cache_dir(self, reclient_cache_dir):
    """Ensure that reclient_cache_exists, create it if it doesn't."""
    self.m.file.ensure_directory('reclient cache directory', reclient_cache_dir)

  def _list_reclient_cache_dir(self, reclient_cache_dir):
    """List contents of the reclient cache directory."""
    self.m.file.listdir('list reclient cache directory', reclient_cache_dir)

  @contextlib.contextmanager
  def _bootstrap_context(
    self,
    buildtools_dir: Path,
    reclient_log_dir: Path,
    reclient_cache_dir,
    bootstrap_extra_env: dict | None = None,
  ):
    """Creates env dict for running bootstrap

    Args:
      reclient_cache_dir: Directory from which to load
                          the dependency cache at reproxy startup
                          and update at shutdown
      bootstrap_extra_env: Additional env vars for reclient to be used by bootstrap.
    """
    reproxy_bin_path = self._get_reclient_exe_path(buildtools_dir, 'reproxy')
    enable_crash_dump = 'true' if self._scandeps_server else 'false'
    use_gce_credentials = 'false' if self._credentials_helper else 'true'
    env = {
      'RBE_instance': self.instance,
      'RBE_log_format': _REPROXY_LOG_FORMAT,
      'RBE_log_dir': reclient_log_dir,
      'RBE_proxy_log_dir': reclient_log_dir,
      'RBE_output_dir': reclient_log_dir,
      'RBE_re_proxy': reproxy_bin_path,
      'RBE_service': self._service,
      'RBE_server_address': self.server_address,
      'RBE_fast_log_collection': 'true',
      'RBE_use_application_default_credentials': 'false',
      'RBE_automatic_auth': 'false',
      'RBE_use_gce_credentials': use_gce_credentials,
      'RBE_fail_early_min_action_count': 4000,
      'RBE_fail_early_min_fallback_ratio': 0.5,
      'RBE_deps_cache_dir': reclient_cache_dir,
      'RBE_cache_dir': reclient_cache_dir,
      'RBE_enable_deps_cache': 'true',
      'RBE_deps_cache_max_mb': _DEPS_CACHE_MAX_MB[self.m.platform.name],
      'RBE_use_unified_uploads': 'true',
      'RBE_use_batching': 'true',
      'RBE_grpc_keepalive_time': '30s',
      'RBE_grpc_keepalive_permit_without_stream': 'false',
      'RBE_auxiliary_metadata_path': 'auxiliary_proto-descriptor-set.proto.bin',
      'GOMA_COMPILER_PROXY_ENABLE_CRASH_DUMP': enable_crash_dump,
      'GOMA_DEPS_CACHE_TABLE_THRESHOLD': 40000,
    }

    if self._credentials_helper:
      env['RBE_credentials_helper'] = self._credentials_helper
    if self._credentials_helper_args:
      env['RBE_credentials_helper_args'] = self._credentials_helper_args

    if self.metrics_project:
      env['RBE_metrics_project'] = self.metrics_project
      env['RBE_metrics_prefix'] = 'go.chromium.org'
      env['RBE_metrics_namespace'] = self.rbe_project
      labels = ''
      builder_id = self.m.buildbucket.build.builder
      if builder_id.project:
        labels += 'project=' + re.sub(r'[=,]', '_', builder_id.project) + ','
      if builder_id.bucket:
        labels += 'bucket=' + re.sub(r'[=,]', '_', builder_id.bucket) + ','
      if builder_id.builder:
        labels += 'builder=' + re.sub(r'[=,]', '_', builder_id.builder) + ','
      labels += (
        'source=' + ('led' if self.m.led.launched_by_led else 'prod') + ','
      )
      labels += 'tool=' + ('siso' if self.m.siso.enabled else 'ninja')
      if labels != '':
        env['RBE_metrics_labels'] = labels

    if self.bootstrap_env is not None:
      env.update(self.bootstrap_env)

    if self._props.profiler_service:
      env['RBE_profiler_service'] = self._props.profiler_service
      env['RBE_profiler_project_id'] = self.rbe_project

    if self.cache_silo:
      env['RBE_cache_silo'] = self.cache_silo

    if self._scandeps_server:
      env['RBE_depsscanner_address'] = (
        "exec://" + self._scandeps_server_bin_path(buildtools_dir)
      )

    if self._ensure_verified:
      env['RBE_mismatch_ignore_config_path'] = self._ignored_mismatches_path

    if bootstrap_extra_env is not None:
      env = dict(bootstrap_extra_env) | env

    with self.m.context(env=env):
      yield

  def _start_reproxy(
    self,
    buildtools_dir: Path,
    reclient_log_dir: Path,
    reclient_cache_dir,
    bootstrap_extra_env: dict | None = None,
  ):
    """Starts the reproxy via bootstrap.

    Args:
      reclient_cache_dir: Directory from which to load
                          the dependency cache at reproxy startup
                          and update at shutdown
    """
    with self._bootstrap_context(
      buildtools_dir, reclient_log_dir, reclient_cache_dir, bootstrap_extra_env
    ):
      self.m.step(
        'start reproxy via bootstrap',
        [self._bootstrap_bin_path(buildtools_dir)],
        infra_step=True,
      )

  def _stop_reproxy(
    self,
    buildtools_dir: Path,
    reclient_log_dir: Path,
    reclient_cache_dir,
  ):
    """Stops the reproxy via bootstrap.

    Args:
      reclient_cache_dir: Directory from which to load
                          the dependency cache at reproxy startup
                          and update at shutdown
    """
    with self._bootstrap_context(
      buildtools_dir, reclient_log_dir, reclient_cache_dir
    ):
      self.m.step(
        'shutdown reproxy via bootstrap',
        [self._bootstrap_bin_path(buildtools_dir), '-shutdown'],
        infra_step=True,
      )

  def _upload_rbe_metrics(self, reclient_log_dir):
    bq_pb = rbe_metrics_bq.RbeMetricsBq()
    bq_pb.build_id = self.m.buildbucket.build.id
    bq_pb.created_at.FromDatetime(self.m.time.utcnow())
    stats_raw = self.m.file.read_raw(
      'load rbe_metrics.pb',
      reclient_log_dir / 'rbe_metrics.pb',
      test_data=make_test_rbe_stats_pb().SerializeToString(),
    )
    try:
      bq_pb.stats.ParseFromString(stats_raw)
    except DecodeError as e:
      self.m.step.empty(
        'upload RBE metrics to BigQuery (FAILED)',
        status=self.m.step.FAILURE,
        log_name='rbe_metrics',
        log_text="DecodeError while parsing rbe_metrics.pb: {}".format(e),
        raise_on_failure=False,
      )
      return

    if self._ensure_verified:
      self._check_mismatch(bq_pb.stats)

    bq_json_dict = json_format.MessageToDict(
      message=bq_pb, preserving_proto_field_name=True
    )
    # All fields in ProxyInfo are map fields and get serialized to JSON maps.
    # Unfortunately, this is incompatible with the corresponding BQ schema,
    # which is are repeated fields and thus expects a JSON arrays.
    proxy_info = bq_pb.stats.proxy_info
    if proxy_info:
      bq_json_dict['stats']['proxy_info'] = [
        proxy_info_to_bq(p) for p in proxy_info
      ]
    if self._disable_bq_upload:
      self.m.step.empty(
        'upload RBE metrics to BigQuery (DISABLED)',
        log_name='rbe_metrics',
        log_text=self.m.json.dumps(bq_json_dict, indent=2),
      )
      return
    bqupload_cipd_path = self.m.cipd.ensure_tool(
      'infra/tools/bqupload/${platform}', 'latest'
    )
    BQ_TABLE_NAME = 'goma-logs.rbe_metrics.builds'
    # `bqupload`'s expected JSON proto format is different from that of the
    # protobuf's native MessageToJson, so we have to dump this json to string on
    # our own.
    try:
      self.m.step(
        'upload RBE metrics to BigQuery',
        [
          bqupload_cipd_path,
          BQ_TABLE_NAME,
        ],
        stdin=self.m.raw_io.input(data=self.m.json.dumps(bq_json_dict)),
        infra_step=True,
      )
    finally:
      self.m.step.active_result.presentation.logs['rbe_metrics'] = (
        self.m.json.dumps(bq_json_dict, indent=2)
      )

  def _check_mismatch(self, stats):
    """Update self._mismatch if there is mismatches."""

    def StatsValue(name):
      for x in stats.stats:
        if x.name == name:
          return x.count
      return None

    num_actions = stats.num_records
    if num_actions == 0:
      # No need to verify because no compiles happened.
      return

    total_verified = StatsValue('LocalMetadata.Verification.TotalVerified')
    if total_verified is None:
      self._mismatch = 'No TotalVerified found in the metrics.'
    if total_verified == 0:
      self._mismatch = 'No compiles are verified.'
    num_mismatches = stats.verification.total_mismatches
    ignored_mismatches = StatsValue(
      'LocalMetadata.Verification.TotalIgnoredMismatches'
    )
    if ignored_mismatches is not None:
      num_mismatches -= ignored_mismatches
    num_fallbacks = sum(
      len(mismatch.remote_digests) == 0 and not mismatch.ignored
      for mismatch in stats.verification.mismatches
    )
    num_mismatches -= num_fallbacks
    if num_mismatches > 0:
      self._mismatch = '%d action(s) mismatched' % num_mismatches

  def _upload_reclient_traces(self, buildtools_dir: Path, reclient_log_dir):
    attributes = ','.join(
      [
        'project=' + self.m.buildbucket.build.builder.project,
        'bucket=' + self.m.buildbucket.build.builder.bucket,
        'builder=' + self.m.buildbucket.builder_name,
        'number=%d' % self.m.buildbucket.build.number,
      ]
    )
    step_result = self.m.step(
      'upload reclient traces',
      [
        self._rpl2cloudtrace_bin_path(buildtools_dir),
        '--project_id',
        self.rbe_project,
        '--proxy_log_dir',
        reclient_log_dir,
        '--attributes',
        attributes,
      ],
      infra_step=True,
    )
    trace_list = (
      'https://console.cloud.google.com/traces/list?project=' + self.rbe_project
    )
    step_result.presentation.links['trace_list'] = trace_list

  def _upload_rpl(self, reclient_log_dir, filename_maker):
    gzip_filename = filename_maker.make_gz('reproxy_rpl')
    gzip_path = self._tmp_base_dir / gzip_filename
    self.m.step(
      name='gzip reproxy RPL',
      cmd=[
        'python3',
        self.resource('generate_rpl_gzip.py'),
        '--reclient-log-dir',
        reclient_log_dir,
        '--output-gzip-path',
        gzip_path,
      ],
      infra_step=True,
    )
    gs_filename = '%s/reclient/%s' % (
      filename_maker.timestamp_date,
      gzip_filename,
    )
    self.m.gsutil.upload(
      gzip_path, _GS_BUCKET, gs_filename, name='upload reproxy RPL'
    )

  def _upload_crash_dumps(
    self, reclient_log_dir, reclient_log_dir_files, filename_maker
  ):
    gzip_filename = filename_maker.make_tgz('reproxy_crash_dumps')
    gzip_path = self._tmp_base_dir / gzip_filename
    dmp_files = [
      file
      for file in reclient_log_dir_files
      if file.pieces[-1].endswith('.dmp')
    ]
    if len(dmp_files) > 0:
      pkg = self.m.archive.package(reclient_log_dir)
      for file in dmp_files:
        pkg.with_file(file)
      pkg.archive('gzip reproxy crash dumps', gzip_path)
      gs_filename = '%s/reclient/%s' % (
        filename_maker.timestamp_date,
        gzip_filename,
      )
      self.m.gsutil.upload(
        gzip_path, _GS_BUCKET, gs_filename, name='upload reproxy crash dumps'
      )

  def _upload_logs(self, reclient_log_dir_files, filename_maker):
    tar_filename = filename_maker.make_tgz('reclient_logs')
    tar_path = self._tmp_base_dir / tar_filename
    log_files = []
    log_suffixes = [
      r'.*\.INFO.*',
      r'.*\.WARNING.*',
      r'.*\.ERROR.*',
      r'.*\.FATAL.*',
      r'.*log$',
      r'.*rpi$',
    ]
    with self.m.step.nest('upload logs'):
      for path in reclient_log_dir_files:
        full_file_name, file_name = str(path), path.pieces[-1]
        if any(re.match(x, file_name) for x in log_suffixes):
          if not file_name.startswith('rewrapper'):
            log_files.append(full_file_name)
      with io.BytesIO() as tar_out:
        with tarfile.open(fileobj=tar_out, mode='w:gz') as tf:
          log_foldername = filename_maker.make('reclient_logs')
          for log in log_files:
            # reclient glog files are generally <100KB, safe to load in memory.
            data_txt = self.m.file.read_text(
              'read %s' % log, log, test_data='fake', include_log=False
            )
            filename = '%s/reclient/%s/%s' % (
              filename_maker.timestamp_date,
              log_foldername,
              os.path.basename(log),
            )
            self.m.gsutil.upload(
              log,
              _GS_BUCKET,
              filename,
              name='upload %s' % (os.path.basename(log)),
            )
            with contextlib.closing(io.BytesIO(data_txt.encode())) as fobj:
              tarinfo = tarfile.TarInfo(os.path.basename(log))
              tarinfo.size = len(fobj.getvalue())
              tarinfo.mtime = time.time()
              tf.addfile(tarinfo, fileobj=fobj)
        tar_data = tar_out.getvalue()
        if self._test_data.enabled:
          tar_data = 'fake tar contents'
        self.m.file.write_raw('create reclient log tar', tar_path, tar_data)
      gs_filename = '%s/reclient/%s' % (
        filename_maker.timestamp_date,
        tar_filename,
      )
      self.m.gsutil.upload(
        tar_path, _GS_BUCKET, gs_filename, name='upload reclient logs'
      )

  @property
  def _cloudtail_exe_path(self):
    if self.m.platform.is_win:
      return 'cloudtail.exe'
    return 'cloudtail'

  @property
  def _cloudtail_wrapper_path(self):
    return self.resource('cloudtail_wrapper.py')

  @property
  def _health_check_path(self):
    return self.resource('perform_health_check.py')

  @property
  def _ignored_mismatches_path(self):
    return self.resource('ignored_mismatches.textproto')

  def _get_cloudtail_pid_file(self, log_name):
    return self._tmp_base_dir.joinpath('cloudtail_' + log_name + '.pid')

  def _start_cloudtail(self, project_id, log_dir, log_name):
    """Start cloudtail to upload reproxy INFO log.

    'cloudtail' binary should be in PATH already.

    Args:
      project_id (str): Cloud project ID
      log_path (str): Path to reproxy's INFO log.

    Raises:
      InfraFailure if it fails to start cloudtail
    """
    cloudtail_args = [
      'python3',
      self._cloudtail_wrapper_path,
      'start',
      '--cloudtail-path',
      self._cloudtail_exe_path,
      '--cloudtail-project-id',
      project_id,
      '--cloudtail-log-path',
      log_dir / log_name,
      '--pid-file',
      self.m.raw_io.output_text(leak_to=self._get_cloudtail_pid_file(log_name)),
    ]

    step_result = self.m.step(
      name='start cloudtail: ' + log_name,
      cmd=cloudtail_args,
      step_test_data=(lambda: self.m.raw_io.test_api.output_text('12345')),
      infra_step=True,
    )
    step_result.presentation.links['cloudtail'] = (
      'https://console.cloud.google.com/logs/viewer?'
      'project=%s&resource=gce_instance%%2F'
      'instance_id%%2F%s' % (project_id, self._hostname)
    )

  def _stop_cloudtail(self, log_name):
    """Stop cloudtail started by _start_cloudtail

    Raises:
      InfraFailure if it fails to stop cloudtail
    """
    self.m.step(
      name='stop cloudtail',
      cmd=[
        'python3',
        self._cloudtail_wrapper_path,
        'stop',
        '--killed-pid-file',
        self._get_cloudtail_pid_file(log_name),
      ],
      infra_step=True,
    )

  def _perform_reclient_health_check(
    self, reclient_log_dir: Path, build_exit_status
  ):
    """Perform reclient health check by verifing existence of FATAL logs

    Raises:
      InfraFailure if health check failed
    """
    self.m.step(
      'perform reclient health check',
      [
        'python3',
        self._health_check_path,
        '--reclient-log-dir',
        reclient_log_dir,
        '--build-exit-status',
        build_exit_status,
      ],
      infra_step=True,
    )

  def _verify_reclient_flags(self, reclient_env):
    str_reclient_env = {}

    for flag, value in reclient_env.items():
      if any(flag.startswith(prefix) for prefix in _VALID_ENV_PREFIX_LIST):
        str_reclient_env[str(flag)] = str(value)
      else:
        raise MalformedREClientFlag(flag)

    return str_reclient_env
