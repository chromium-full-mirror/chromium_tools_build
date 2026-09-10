# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections.abc
import contextlib
import urllib

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

from RECIPE_MODULES.depot_tools import bot_update

_RESULT_DETAILS_LINK = 'result_details (logcats, flakiness links)'


class AndroidApi(recipe_api.RecipeApi):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._adb_path: Path | None = None
    self._devices = None

  @property
  def devices(self):
    assert self._devices is not None, (
      'devices is only available after device_status()'
    )
    return self._devices

  def adb_path(self, source_dir: Path) -> Path:
    return self._adb_path or self.m.adb.default_adb_path(source_dir)

  def out_path(self, source_dir: Path) -> Path:
    return source_dir / 'out'

  def devil_dir(self, source_dir: Path) -> Path:
    return source_dir / 'third_party/catapult/devil'

  @property
  def known_devices_file(self):
    return self.m.path.join(
      self.m.path.expanduser('~'), '.android', 'known_devices.json'
    )

  def make_zip_archive(
    self,
    step_name,
    archive_name,
    build_dir,
    files=None,
    preserve_paths=True,
    include_filters=None,
    exclude_filters=None,
    **kwargs,
  ):
    """Creates and stores the archive file.

    Args:
      step_name: Name of the step.
      archive_name: Name of the archive file.
      files: If specified, only include files here instead of in build_dir.
      build_dir: Path to the build dir.
      preserve_paths: If True, files will be stored using the subdirectories
        in the archive.
      include_filters: List of globs to be included in the archive.
      exclude_filters: List of globs to be excluded from the archive.
    """
    cmd = [
      'vpython3',
      self.resource('archive_build.py'),
      '--build-dir',
      build_dir,
      '--name',
      archive_name,
    ]

    # TODO(luqui): Clean up when these are covered by the external builders.
    if files:  # pragma: no cover
      cmd.extend(['--files', ','.join(files)])
    if include_filters:
      for f in include_filters:
        cmd.extend(['--include-filter', f])
    if exclude_filters:
      for f in exclude_filters:
        cmd.extend(['--exclude-filter', f])
    if not preserve_paths:  # pragma: no cover
      cmd.append('--ignore-subfolder-names')

    self.m.step(step_name, cmd, infra_step=True, **kwargs)

  def clean_local_files(self, source_dir: Path):
    target = self.m.chromium.c.BUILD_CONFIG
    debug_info_dumps = source_dir / f'out/{target}/debug_info_dumps'
    test_logs = source_dir / f'out/{target}/test_logs'
    build_product = source_dir / 'out/build_product.zip'
    cmd = [
      'vpython3',
      self.resource('clean_local_files.py'),
      debug_info_dumps,
      test_logs,
      build_product,
      source_dir,
    ]
    self.m.step('clean local files', cmd, infra_step=True)

  def run_tree_truth(
    self,
    update_result: bot_update.Result,
    additional_repos: collections.abc.Iterable[str] | None = None,
  ) -> None:
    # TODO(sivachandra): The downstream ToT builder will require
    # 'Show Revisions' step.
    repos = list(update_result.fixed_revisions)
    if additional_repos:
      repos.extend(additional_repos)
    source_dir = update_result.source_root.path
    self.m.step(
      'tree truth steps',
      [source_dir / 'build/tree_truth.sh', source_dir] + repos,
    )

  def upload_build(self, source_dir: Path, build_dir: Path, bucket, path):
    """Uploads a build zip to GS.

    Args:
      source_dir: Path to source checkout.
      build_dir: Path to build dir.
      bucket: GS bucket to upload to.
      path: Path inside GS bucket to upload to.
    """
    archive_name = 'build_product.zip'

    zipfile = source_dir / f'out/{archive_name}'

    with self.m.context(cwd=source_dir):
      self.make_zip_archive(
        'zip_build_product',
        archive_name,
        build_dir,
        preserve_paths=True,
        exclude_filters=[
          "obj/*",
          "gen/*",  # Default toolchain's obj/ and gen/
          "*/obj/*",
          "*/gen/*",  # Secondary toolchains' obj/ and gen/
          "*/thinlto-cache/*",  # ThinLTO cache directory
          "*.stamp",
          "*.d",  # Files used only for incremental builds
          "*.ninja",
          ".ninja_*",  # Build files, .ninja_log, .ninja_deps
        ],
      )

    self.m.gsutil.upload(
      name='upload_build_product', source=zipfile, bucket=bucket, dest=path
    )

  def download_build(
    self, source_dir: Path, bucket, path, *, extract_path=None, globs=None
  ):
    zipfile = source_dir / 'out/build_product.zip'
    self.m.gsutil.download(
      name='download_build_product', bucket=bucket, source=path, dest=zipfile
    )
    extract_path = extract_path or source_dir
    globs = globs or []
    with self.m.context(cwd=extract_path):
      self.m.step(
        'unzip_build_product',
        ['unzip', '-o', zipfile] + globs,
        infra_step=True,
      )

  def use_devil_adb(self, source_dir: Path):
    # TODO(crbug.com/1067294): Remove this after resolving.
    devil_path = self.devil_dir(source_dir)
    cmd = ['vpython3', self.resource('initialize_devil.py'), devil_path]
    self.m.step('initialize devil', cmd)
    self._adb_path = devil_path / 'bin/deps/linux2/x86_64/bin/adb'

  def create_adb_symlink(self, source_dir: Path):
    # Creates a sym link to the adb executable in the home dir
    cmd = [
      'vpython3',
      source_dir / 'build/symlink.py',
      '-f',
      self.adb_path(source_dir),
      self.m.path.join('~', 'adb'),
    ]
    self.m.step('create adb symlink', cmd, infra_step=True)

  @property
  def _logcat_dir(self):
    return self.m.path.cleanup_dir / 'logcat'

  def spawn_logcat_monitor(self, source_dir: Path):
    with self.m.context(env=self.m.chromium.get_env(source_dir)):
      self.m.step(
        'spawn_logcat_monitor',
        [
          'vpython3',
          self.repo_resource('recipes', 'daemonizer.py'),
          '--',
          source_dir / 'build/android/adb_logcat_monitor.py',
          self._logcat_dir,
          self.adb_path(source_dir),
        ],
        infra_step=True,
      )

  def spawn_device_monitor(self, source_dir: Path):
    device_monitor_script = (
      self.devil_dir(source_dir) / 'devil/android/tools/device_monitor.py'
    )
    self.m.step(
      'spawn_device_monitor',
      [
        'vpython3',
        self.repo_resource('recipes', 'daemonizer.py'),
        '--action',
        'restart',
        '--pid-file-path',
        '/tmp/device_monitor.pid',
        '--',
        device_monitor_script,
        '--adb-path',
        self.adb_path(source_dir),
        '--denylist-file',
        self.denylist_file(source_dir),
      ],
      infra_step=True,
    )

  def shutdown_device_monitor(self):
    self.m.step(
      'shutdown_device_monitor',
      [
        'vpython3',
        self.repo_resource('recipes', 'daemonizer.py'),
        '--action',
        'stop',
        '--pid-file-path',
        '/tmp/device_monitor.pid',
      ],
      infra_step=True,
    )

  def authorize_adb_devices(self, source_dir: Path):
    with self.m.context(env=self.m.chromium.get_env(source_dir)):
      return self.m.step(
        'authorize_adb_devices',
        [
          'vpython3',
          self.resource('authorize_adb_devices.py'),
          '--verbose',
          '--adb-path',
          self.adb_path(source_dir),
        ],
        infra_step=True,
      )

  def denylist_file(self, source_dir: Path) -> Path:
    return self.out_path(source_dir) / 'bad_devices.json'

  def non_denylisted_devices(self, source_dir: Path):
    denylist_file = self.denylist_file(source_dir)
    if not self.m.path.exists(denylist_file):
      return self.devices
    denylisted_devices = self.m.file.read_json(
      'read_denylist_file', denylist_file
    )
    return [s for s in self.devices if s not in denylisted_devices]

  def device_recovery(self, source_dir: Path, **kwargs):
    cmd = [
      'vpython3',
      (self.devil_dir(source_dir) / '/devil/android/tools/device_recovery.py'),
      '--denylist-file',
      self.denylist_file(source_dir),
      '--known-devices-file',
      self.known_devices_file,
      '--adb-path',
      self.adb_path(source_dir),
      '-v',
    ]
    with self.m.context(env=self.m.chromium.get_env(source_dir)):
      self.m.step('device_recovery', cmd, infra_step=True, **kwargs)

  def device_status(self, source_dir: Path, **kwargs):
    buildbot_file = '/home/chrome-bot/.adb_device_info'
    args = [
      '--json-output',
      self.m.json.output(),
      '--denylist-file',
      self.denylist_file(source_dir),
      '--known-devices-file',
      self.known_devices_file,
      '--buildbot-path',
      buildbot_file,
      '--adb-path',
      self.adb_path(source_dir),
      '-v',
      '--overwrite-known-devices-files',
    ]
    try:
      with self.m.context(env=self.m.chromium.get_env(source_dir)):
        result = self.m.step(
          'device_status',
          [
            'vpython3',
            (
              self.devil_dir(source_dir)
              / 'devil/android/tools/device_status.py'
            ),
          ]
          + args,
          step_test_data=lambda: self.m.json.test_api.output(
            [
              {
                "battery": {
                  "status": "5",
                  "scale": "100",
                  "temperature": "249",
                  "level": "100",
                  "AC powered": "false",
                  "health": "2",
                  "voltage": "4286",
                  "Wireless powered": "false",
                  "USB powered": "true",
                  "technology": "Li-ion",
                  "present": "true",
                },
                "wifi_ip": "",
                "imei_slice": "Unknown",
                "ro.build.id": "LRX21O",
                "ro.build.product": "product_name",
                "build_detail": "google/razor/flo:5.0/LRX21O/1570415:userdebug/dev-keys",
                "serial": "07a00ca4",
                "adb_status": "device",
                "denylisted": False,
                "usb_status": True,
              },
              {
                "adb_status": "offline",
                "denylisted": True,
                "serial": "03e0363a003c6ad4",
                "usb_status": False,
              },
              {
                "adb_status": "unauthorized",
                "denylisted": True,
                "serial": "03e0363a003c6ad5",
                "usb_status": True,
              },
              {
                "adb_status": "device",
                "denylisted": True,
                "serial": "03e0363a003c6ad6",
                "usb_status": True,
              },
            ]
          ),
          infra_step=True,
          **kwargs,
        )
      self._devices = []
      offline_device_index = 1
      for d in result.json.output:
        try:
          if not d['usb_status']:
            key = '%s: missing' % d['serial']
          elif d['adb_status'] != 'device':
            key = '%s: adb status %s' % (d['serial'], d['adb_status'])
          elif d['denylisted']:
            key = '%s: denylisted' % d['serial']
          else:
            key = '%s %s %s' % (
              d['ro.build.product'],
              d['ro.build.id'],
              d['serial'],
            )
            self._devices.append(d['serial'])
        except KeyError:
          key = 'unknown device %d' % offline_device_index
          offline_device_index += 1
        result.presentation.logs[key] = self.m.json.dumps(
          d, indent=2
        ).splitlines()
      result.presentation.step_text = 'Online devices: %s' % len(self._devices)
      return result
    except self.m.step.InfraFailure as f:
      params = [
        (
          'summary',
          (
            'Device Offline on %s %s'
            % (self.m.builder_group.for_current, self.m.properties['bot_id'])
          ),
        ),
        (
          'comment',
          (
            'Buildbot: %s\n(Please do not change any labels)'
            % self.m.buildbucket.builder_name
          ),
        ),
        ('labels', 'Restrict-View-Google,OS-Android,Infra,Infra-Labs'),
      ]
      link = (
        'https://code.google.com/p/chromium/issues/entry?%s'
        % urllib.parse.urlencode(params)
      )
      f.result.presentation.links.update({'report a bug': link})
      raise

  def provision_devices(
    self,
    source_dir: Path,
    *,
    skip_wipe=False,
    disable_location=False,
    reboot_timeout=None,
    emulators=False,
    **kwargs,
  ):
    if self.c and self.c.use_devil_provision:
      provision_path = (
        self.devil_dir(source_dir) / 'devil/android/tools/provision_devices.py'
      )
    else:
      provision_path = source_dir / 'build/android/provision_devices.py'
    cmd = [
      'vpython3',
      provision_path,
      '--adb-path',
      self.adb_path(source_dir),
      '--denylist-file',
      self.denylist_file(source_dir),
      '--output-device-denylist',
      self.m.json.output(add_json_log=False),
      '-t',
      self.m.chromium.c.BUILD_CONFIG,
      '-v',
    ]
    if skip_wipe:
      cmd.append('--skip-wipe')
    if disable_location:
      cmd.append('--disable-location')
    if reboot_timeout is not None:
      assert isinstance(reboot_timeout, int)
      assert reboot_timeout > 0
      cmd.extend(['--reboot-timeout', reboot_timeout])
    if self.c and self.c.remove_system_packages:
      cmd.append('--remove-system-packages')
      cmd.extend(self.c.remove_system_packages)
    if emulators:
      cmd.append('--emulators')
    with self.m.context(env=self.m.chromium.get_env(source_dir)):
      with self.handle_exit_codes():
        return self.m.step('provision_devices', cmd, infra_step=True, **kwargs)

  def create_result_details(
    self,
    source_dir: Path,
    step_name,
    json_results_file,
  ):
    try:
      cmd = [
        'vpython3',
        (
          source_dir
          / 'build/android/pylib/results/presentation/test_results_presentation.py'
        ),
        '--json-file',
        json_results_file,
        '--test-name',
        step_name,
        '--builder-name',
        self.m.buildbucket.builder_name,
        '--build-number',
        self.m.buildbucket.build.number,
        '--cs-base-url',
        self.c.cs_base_url,
        '--bucket',
        self.c.results_bucket,
      ]
      result_details = self.m.step(
        '%s: generate result details' % step_name,
        cmd,
        stdout=self.m.raw_io.output_text(),
        step_test_data=(
          lambda: self.m.raw_io.test_api.stream_output_text(
            'Result Details: https://storage.cloud.google.com/'
            'chromium-result-details'
          )
        ),
      )
      # Stdout is in the format of 'Result Details: <link>'.
      lines = result_details.stdout.strip()
      prefix = 'Result Details: '
      return (
        lines.splitlines()[0][len(prefix) :] if lines.startswith(prefix) else ''
      )
    except self.m.step.StepFailure:
      return (
        'https://storage.googleapis.com/chromium-result-details/'
        'UploadQuietFailure.txt'
      )

  def logcat_dump(self, source_dir: Path, build_dir: Path):
    if self.c.logcat_bucket:
      log_path = build_dir / 'full_log'
      cmd = [
        'vpython3',
        source_dir / 'build/android/adb_logcat_printer.py',
        '--output-path',
        log_path,
        self._logcat_dir,
      ]
      self.m.step('logcat_dump', cmd, infra_step=True)
      args = []
      if self.m.tryserver.is_tryserver and not self.c.INTERNAL:
        args += ['-a', 'public-read']
      self.m.gsutil.upload(
        log_path,
        self.c.logcat_bucket,
        'logcat_dumps/%s/%s'
        % (self.m.buildbucket.builder_name, self.m.buildbucket.build.number),
        args=args,
        link_name='logcat dump',
        parallel_upload=True,
      )

    else:
      cmd = [
        'vpython3',
        self.repo_resource('recipes', 'tee.py'),
        build_dir / 'full_log',
        '--',
        source_dir / 'build/android/adb_logcat_printer.py',
        self._logcat_dir,
      ]
      self.m.step('logcat_dump', cmd, infra_step=True)

  def generate_breakpad_symbols(
    self, symbols_dir, binary_path, root_chromium_dir
  ):
    """Generate breakpad symbols.

    This step requires dump_syms binary to exist in the build dir.

    Args:
      symbols_dir: The directory to dump the breakpad symbols to.
      binary_path: Path to binary to generate symbols for.
      root_chromium_dir: Root Chromium directory.
    """
    build_dir = root_chromium_dir.joinpath(
      'out', self.m.chromium.c.BUILD_CONFIG
    )

    cmd = [
      'vpython3',
      root_chromium_dir.joinpath(
        'components',
        'crash',
        'content',
        'tools',
        'generate_breakpad_symbols.py',
      ),
      '--symbols-dir',
      symbols_dir,
      '--build-dir',
      build_dir,
      '--binary',
      binary_path,
    ]
    self.m.step(
      ('generate breakpad symbols for %s' % self.m.path.basename(binary_path)),
      cmd,
    )

  def stackwalker(self, source_dir: Path, binary_paths):
    """Runs stack walker tool to symbolize breakpad crashes.

    This step requires logcat file. The logcat monitor must have
    been run on the bot.

    Args:
      source_dir: The path to the top-level repo.
      binary_paths: Paths to binaries to generate breakpad symbols.
    """
    build_dir = self.m.chromium.default_build_dir(source_dir)
    logcat = build_dir / 'full_log'

    dump_syms_path = build_dir / 'dump_syms'
    microdump_stackwalk_path = build_dir / 'microdump_stackwalk'
    required_binaries = binary_paths + [
      microdump_stackwalk_path,
      dump_syms_path,
    ]
    if not all(map(self.m.path.exists, required_binaries)):
      result = self.m.step(
        'skipping stackwalker step',
        [
          'echo',
          'Missing: %s'
          % ' '.join(
            [str(b) for b in required_binaries if not self.m.path.exists(b)]
          ),
        ],
      )
      result.presentation.logs['info'] = [
        'This bot appears to not have some of the binaries required to run ',
        'stackwalker. No action is needed at this time; contact infra-dev@ ',
        'for any questions or issues',
      ]
      return

    temp_symbols_dir = self.m.path.mkdtemp('symbols')
    # TODO(mikecase): Only generate breakpad symbols if we
    # know there is at least one breakpad crash. This step takes
    # several minutes and we should only run it if we need to.
    for binary in binary_paths:
      self.generate_breakpad_symbols(temp_symbols_dir, binary, source_dir)
    cmd = [
      'vpython3',
      source_dir / 'build/android/stacktrace/stackwalker.py',
      '--stackwalker-binary-path',
      microdump_stackwalk_path,
      '--stack-trace-path',
      logcat,
      '--symbols-path',
      temp_symbols_dir,
    ]
    self.m.step('symbolized breakpad crashes', cmd)

  def stack_tool_steps(self, source_dir: Path, *, force_latest_version=False):
    build_dir = self.m.chromium.default_build_dir(source_dir)
    log_file = build_dir / 'full_log'

    target_arch = self.m.chromium.get_build_target_arch()

    # --output-directory hasn't always exited on these scripts, so use the
    # CHROMIUM_OUTPUT_DIR environment variable to avoid unrecognized flag
    # failures on older script versions (e.g. when doing bisects).
    # TODO(agrieve): Switch to --output-directory once we don't need bisects
    #     to be able to try revisions that happened before Feb 2016.
    env = self.m.chromium.get_env(source_dir)
    env['CHROMIUM_OUTPUT_DIR'] = str(build_dir)
    with self.m.context(env=env):
      self.m.step(
        'stack_tool_with_logcat_dump',
        [
          (
            source_dir
            / 'third_party/android_platform/development/scripts/stack'
          ),
          '--arch',
          target_arch,
          '--more-info',
          log_file,
        ],
        infra_step=True,
      )
    tombstones_cmd = [
      source_dir / 'build/android/tombstones.py',
      '-a',
      '-s',
      '-w',
    ]
    if (
      force_latest_version
      or int(self.m.chromium.get_version(source_dir).get('MAJOR', 0)) > 52
    ):
      tombstones_cmd += ['--adb-path', self.adb_path(source_dir)]
    with self.m.context(env=env):
      self.m.step('stack_tool_for_tombstones', tombstones_cmd, infra_step=True)

  def common_tests_setup_steps(self, source_dir: Path, **provision_kwargs):
    if self.c.use_devil_adb:
      self.use_devil_adb(source_dir)
    self.create_adb_symlink(source_dir)
    self.spawn_logcat_monitor(source_dir)
    self.spawn_device_monitor(source_dir)
    self.authorize_adb_devices(source_dir)
    self.device_recovery(source_dir)
    self.provision_devices(source_dir, **provision_kwargs)
    self.device_status(source_dir)

  def common_tests_final_steps(
    self,
    source_dir: Path,
    build_dir: Path,
    *,
    force_latest_version=False,
    run_stackwalker=False,
  ):
    self.shutdown_device_monitor()
    self.logcat_dump(source_dir, build_dir)
    self.stack_tool_steps(source_dir, force_latest_version=force_latest_version)

    if run_stackwalker:
      binary_dir = build_dir / 'lib.unstripped'
      breakpad_binaries = [binary_dir / 'libchrome.so']
      if self.m.path.exists(binary_dir / 'libwebviewchromium.so'):
        breakpad_binaries.append(binary_dir / 'libwebviewchromium.so')
      self.stackwalker(source_dir=source_dir, binary_paths=breakpad_binaries)

  def run_test_suite(
    self,
    source_dir: Path,
    build_dir: Path,
    suite,
    *,
    verbose=True,
    result_details=False,
    store_tombstones=False,
    name=None,
    json_results_file=None,
    shard_timeout=None,
    args=None,
    **kwargs,
  ):
    args = args or []
    args.extend(['--denylist-file', self.denylist_file(source_dir)])
    if verbose:
      args.append('--verbose')
    if result_details and not json_results_file:
      json_results_file = self.m.test_utils.gtest_results(add_json_log=False)
    if json_results_file:
      args.extend(['--json-results-file', json_results_file])
    if store_tombstones:
      args.append('--store-tombstones')
    # TODO(agrieve): Remove once no more tests pass shard_timeout (contained in
    #     wrapper scripts).
    if shard_timeout:
      args.extend(['-t', str(shard_timeout)])
    step_name = name or str(suite)
    try:
      with self.m.context(env=self.m.chromium.get_env(source_dir)):
        self.test_runner(
          source_dir,
          build_dir,
          step_name,
          args=args,
          wrapper_script_suite_name=suite,
          **kwargs,
        )
    finally:
      result_step = self.m.step.active_result
      if result_details:
        if hasattr(result_step, 'test_utils') and hasattr(
          result_step.test_utils, 'gtest_results'
        ):
          json_results = self.m.json.input(
            result_step.test_utils.gtest_results.raw
          )
          details_link = self.create_result_details(
            source_dir, step_name, json_results
          )
          self.m.step.active_result.presentation.links[_RESULT_DETAILS_LINK] = (
            details_link
          )

  @contextlib.contextmanager
  def handle_exit_codes(self):
    """Handles exit codes emitted by the test runner and other scripts."""
    EXIT_CODES = {
      'error': 1,
      'infra': 87,
      'warning': 88,
    }
    try:
      yield
    except self.m.step.StepFailure as f:
      if f.result.retcode == EXIT_CODES['infra']:
        i = self.m.step.InfraFailure(f.name or f.reason, result=f.result)
        i.result.presentation.status = self.m.step.EXCEPTION
        raise i from f
      if f.result.retcode == EXIT_CODES['warning']:
        w = self.m.step.StepWarning(f.name or f.reason, result=f.result)
        w.result.presentation.status = self.m.step.WARNING
        raise w from f
      if f.result.retcode == EXIT_CODES['error']:
        f.result.presentation.status = self.m.step.FAILURE
      raise

  def test_runner(
    self,
    source_dir: Path,
    build_dir: Path,
    step_name,
    *,
    args=None,
    wrapper_script_suite_name,
    pass_adb_path=True,
    # TODO(crbug.com/1108016): Once resultdb is enabled globally,
    # makes resultdb as a required param.
    resultdb=None,
    **kwargs,
  ):
    """Wrapper for the python testrunner script.

    Args:
      step_name: Name of the step.
      args: Testrunner arguments.
      wrapper_script_suite_name: Name of wrapper_script_suite
      pass_adb_path: If True, pass the adb path with --adb-path.
      resultdb: None or chromium_tests.steps.ResultDB instance. If set with
        True in resultdb.enable, the test will be executed with ResultSink.
    """
    if not args:  # pragma: no cover
      args = []
    if pass_adb_path:
      args.extend(['--adb-path', self.adb_path(source_dir)])
    with self.handle_exit_codes():
      script = source_dir / self.c.test_runner
      env = {}
      script = build_dir / f'bin/run_{wrapper_script_suite_name}'

      with self.m.context(env=env):
        cmd = [script] + args
        if resultdb and resultdb.enable:
          cmd = resultdb.wrap(self.m, cmd, step_name=step_name)
        return self.m.step(step_name, cmd, **kwargs)
