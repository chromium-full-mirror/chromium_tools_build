# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
import contextlib
from functools import cached_property
import json
import re

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2
from RECIPE_MODULES.build.v8_tests import testing as v8testing
from RECIPE_MODULES.build.v8.v8version import VersionTuple, normalize_version

from .platforms import UnixPlatform, WindowsPlatform
from .profile_track import (
    ChangeProfileTrack,
    RevisionProfileTrack,
    VersionProfileTrack,
)

JET_STREAM_BASE_PATH = 'benchmarks/JetStream'
BUCKET_NAME = 'chromium-v8-builtins-pgo'
GERRIT_HOST = 'https://chromium-review.googlesource.com'
GERRIT_PROJECT = 'v8/v8'
V8_REPO_URL = 'https://chromium.googlesource.com/v8/v8/'
V8_PERF_REPO_URL = 'https://chrome-internal.googlesource.com/v8/v8-perf'

JET_STREAM_VERSION_PATTERN = r'default=Path\(\'\./(\w+)/cli.js\'\)'
JET_STREAM_VERSION_TEST_DATA = '  default=Path(\'./JetStream2/cli.js\'),'

# Define the hours passed for each retry after the initial run, e.g. [0, 6, 24]
# retries profile building after 0 hours, 6 hours, and 24 hours. If they all
# fail, a total of four attempts have been made.
RETRY_INTERVAL = [0, 6, 24]

# Do not retry if the first version failure is older than the specified value.
RETRY_TIMEOUT = 24 * 5

BLOCKLIST_BUCKET = 'chromium-v8-builtins-pgo-state'
BLOCKLIST_FILE = 'blocked-versions.txt'
BLOCKLIST_PATH = f'gs://{BLOCKLIST_BUCKET}/{BLOCKLIST_FILE}'

VERSION_TAG_PATTERN = r'(\w+)\s+refs/tags/(\d+\.\d+\.\d+(?:\.\d+)?)'
PGO_VERSION_TAG_PATTERN = r'(\w+)\s+refs/tags/(\d+\.\d+\.\d+(?:\.\d+)?)-pgo'
PROFILE_PATTERN = (r'(block_hint,\w+(,\d+){3}\n)+'
                   r'(builtin_count,\w+,\d+\n)*'
                   r'(block_count,\w+(,\d+){2}\n)*'
                   r'(builtin_hash,\w+,\-?\d+\n)+')

COMPILATOR_CAS_INSTANCE = 'projects/chromium-swarm/instances/default_instance'

COMPILATORS = [
    {
        'triggered_by': {'ci', 'ci-hp'},
        'project': 'v8',
        'bucket': 'ci',
        'compilators': {
            'x86': 'V8 Linux PGO instrumentation - builder',
            'x64': 'V8 Linux64 PGO instrumentation - builder',
            'x86-rl': 'V8 Win32 PGO instrumentation - builder',
            'x64-rl': 'V8 Win64 PGO instrumentation - builder',
        },
        'profiling_pool': 'chromium.tests',
    },
    {
        'triggered_by': {'try'},
        'project': 'v8',
        'bucket': 'try',
        'compilators': {
            'x86': 'v8_linux_pgo_compile_rel',
            'x64': 'v8_linux64_pgo_compile_rel',
            'x86-rl': 'v8_win_pgo_compile_rel',
            'x64-rl': 'v8_win64_pgo_compile_rel',
        },
        'profiling_pool': 'chrome.tests',
    },
]

PLATFORM_BY_TARGET = {
    'x86': UnixPlatform(),
    'x64': UnixPlatform(),
    'x86-rl': WindowsPlatform(),
    'x64-rl': WindowsPlatform(),
}


def with_wrapper_step(func):

  def wrapped_func(*args, **kwargs):
    builder = args[0]
    assert hasattr(
        builder, 'api'), ('Decorated function does not take recipe API object '
                          'as first argument.')

    wrapper_step_name = func.__name__.replace('_', ' ')
    with builder.api.step.nest(wrapper_step_name) as step:
      step.logs['profile trackers'] = [
          t.presentation for t in builder.profile_trackers or []
      ]
      return func(*args, **kwargs)

  return wrapped_func


class BaseProfileBuilder(ABC):

  def __init__(self, api, compilators):
    self.api = api
    self.profile_trackers = None
    self.perf_code_path = None
    self.selected_compilator_ids = compilators

  @cached_property
  def compilators(self):
    triggered_by = self.api.buildbucket.build.builder.bucket
    bucket_compilators = next(
        c for c in COMPILATORS if triggered_by in c['triggered_by'])

    compilator_ids = self.selected_compilator_ids
    return {
        platform: {
            'project': bucket_compilators['project'],
            'bucket': bucket_compilators['bucket'],
            'builder': name,
            'profiling_pool': bucket_compilators['profiling_pool'],
        }
        for platform, name in bucket_compilators['compilators'].items()
        if compilator_ids is None or platform in compilator_ids
    }

  def run(self):
    with self.api.context(cwd=self.work_dir):
      self.init_trackers_for_candidate_versions()

      if not self.profile_trackers:
        return result_pb2.RawResult(status=common_pb.SUCCESS)

      self.trigger_compilators()
      self.download_benchmark_code()
      self.collect_compilation_isolates()
      self.merge_isolate_with_benchmark()
      self.trigger_profilers()
      self.collect_profiles()
      self.validate_profiles()
      self.upload_to_gs()
      self.assign_pgo_tags()
      self.add_comment_to_gerrit_changes()
      self.report_exceptions()
      return self.get_build_result()

  @cached_property
  def work_dir(self):
    return self.api.path.mkdtemp()

  @cached_property
  def orchestrator(self):
    return self.api.v8_orchestrator.create_compilator_handler()

  @property
  def healthy_profile_trackers(self):
    return [t for t in self.profile_trackers if not t.exception]

  @property
  def healthy_profile_trackers_by_version(self):
    groups = defaultdict(list)
    for t in self.healthy_profile_trackers:
      groups[t.version].append(t)
    return list(groups.values())

  @property
  def failed_profile_trackers(self):
    return [t for t in self.profile_trackers if t.exception]

  @with_wrapper_step
  def trigger_compilators(self):
    for tracker in self.healthy_profile_trackers:
      with self.exception_capture(tracker), self.api.step.nest(tracker.name):
        compilator = self.compilators[tracker.track]
        tracker.compilator_handler = self.orchestrator.trigger_compilator(
            compilator['builder'],
            project=compilator['project'],
            bucket=compilator['bucket'],
            **tracker.compilator_kwargs,
        )

  @with_wrapper_step
  def collect_compilation_isolates(self):
    for tracker in self.healthy_profile_trackers:
      with self.exception_capture(tracker), self.api.step.nest(tracker.name):
        sub_build = self.orchestrator.launch_compilator_watcher(
            tracker.compilator_handler)
        comp_props = sub_build.output.properties['compilator_properties']
        tracker.find_original_cas_digest(comp_props)

  def get_jetstream_version(self, tracker, cas_work_dir):
    """Establish the JetStream version and subfolder from the V8 PGO script."""

    # TODO(https://crbug.com/487336000): Make this more robust by using a
    # well-defined property for the JetStream version. E.g. by executing this
    # script, letting it return that value. However, this needs to be first
    # implemented on the V8 side and reach all relevant channels.
    content = self.api.file.read_text(
        name=f'check jetstream version for {tracker.name}',
        source=cas_work_dir / 'tools/builtins-pgo/profile_only.py',
        test_data=JET_STREAM_VERSION_TEST_DATA,
        include_log=False)

    match = re.search(JET_STREAM_VERSION_PATTERN, content)
    assert match, 'Could not establish JetStream version'

    version = match.group(1)
    return version

  @with_wrapper_step
  def merge_isolate_with_benchmark(self):
    for tracker in self.healthy_profile_trackers:
      with self.exception_capture(tracker), self.api.step.nest(tracker.name):
        cas_work_dir = self.api.path.mkdtemp()

        with self.api.cas.with_instance(COMPILATOR_CAS_INSTANCE):
          self.api.cas.download('download', tracker.original_cas_digest,
                                cas_work_dir)

        version = self.get_jetstream_version(tracker, cas_work_dir)
        self.api.file.copytree('copy benchmark code',
                               self.perf_code_path / version,
                               cas_work_dir / version)
        tracker.augmented_cas_digest = self.api.cas.archive(
            'archive', cas_work_dir)

  @with_wrapper_step
  def trigger_profilers(self):
    # The default priority is 200 (very low). Presubmit runs at 30.
    # FYI tasks run at 40.
    self.api.chromium_swarming.default_priority = 25

    for tracker in self.healthy_profile_trackers:
      with self.exception_capture(tracker), self.api.step.nest(tracker.name):
        platform = PLATFORM_BY_TARGET[tracker.track]
        task = self.api.chromium_swarming.task(
            name=f'pgo profile {tracker.name}',
            task_output_dir=tracker.profile_dir,
            raw_cmd=[
                'vpython3',
                '-u',
                platform.profile_only_path,
                '--d8-path',
                platform.d8_out_path,
                '--output-dir',
                '${ISOLATED_OUTDIR}',
            ],
            cas_input_root=tracker.augmented_cas_digest,
        )

        task_slice = task.request[0]
        task_dimensions = task_slice.dimensions
        task_dimensions.update(tracker.swarming_dimensions)
        task_slice = task_slice.with_dimensions(**task_dimensions)

        v8testing.override_swarming_attrs(task, task_slice,
                                          dict(tracker.swarming_task_attrs))

        self.api.chromium_swarming.trigger_task(task)
        tracker.profile_task = task

  @with_wrapper_step
  def collect_profiles(self):
    for tracker in self.healthy_profile_trackers:
      with self.exception_capture(tracker), self.api.step.nest(tracker.name):
        step, is_valid = self.api.chromium_swarming.collect_task(
            tracker.profile_task)
        if step.presentation.status != self.api.step.SUCCESS or not is_valid:
          raise Exception(
              f'collect_profiles returned {step.presentation.status}')

  @with_wrapper_step
  def validate_profiles(self):
    """Check profiles against a static structure to prevent malicious bots from
    submitting arbitrary content which might affect the build process."""

    for tracker in self.healthy_profile_trackers:
      assert tracker.profile_out_file, 'Expected profile, but no file found.'

      content = self.api.file.read_text(
          name=f'read profile for {tracker.name}',
          source=tracker.profile_out_file,
          include_log=False)
      assert re.fullmatch(PROFILE_PATTERN, content), (
          f'Profile from tracker {tracker.name} does not match expected pattern.'
      )

  @with_wrapper_step
  def download_benchmark_code(self):
    self.api.v8.git_output(
        'clone',
        '--depth=1',
        '--filter=blob:none',
        '--sparse',
        V8_PERF_REPO_URL,
    )

    config = self.api.gclient.make_config()
    solution = config.solutions.add()
    solution.name = 'v8-perf'
    solution.url = V8_PERF_REPO_URL
    solution.managed = False
    self.api.gclient(
        'setup',
        ['config', '--spec',
         self.api.gclient.config_to_pythonish(config)])

    # We only check out the JetStream root. With `gclient sync`, all three
    # JetStream benchmarks are synced.
    checkout_path = self.work_dir / 'v8-perf'
    with self.api.context(cwd=checkout_path):
      self.api.v8.git_output(
          'sparse-checkout',
          'set',
          JET_STREAM_BASE_PATH,
      )

      self.api.gclient.sync(cfg=config)
      self.perf_code_path = checkout_path / 'benchmarks'

  @contextlib.contextmanager
  def exception_capture(self, tracker):
    try:
      yield
    except Exception as ex:
      presentation = self.api.step.active_result.presentation
      presentation.step_text = str(ex)
      tracker.exception = ex

  @abstractmethod
  def init_trackers_for_candidate_versions(self):
    pass  # pragma: no cover

  def upload_to_gs(self):
    pass

  def assign_pgo_tags(self):
    pass

  def add_comment_to_gerrit_changes(self):
    pass

  def report_exceptions(self):
    pass

  def get_build_result(self):
    failed_trackers = self.failed_profile_trackers

    if not failed_trackers:
      return result_pb2.RawResult(status=common_pb.SUCCESS)

    msg = (f'{len(failed_trackers)} of {len(self.profile_trackers)} tracks '
           'encounterd exceptions.')
    return result_pb2.RawResult(status=common_pb.FAILURE, summary_markdown=msg)


class V8CiBuilder(BaseProfileBuilder):

  def __init__(self, api, compilators, revision):
    super().__init__(api, compilators)
    self.revision = revision

  @with_wrapper_step
  def init_trackers_for_candidate_versions(self):
    self.profile_trackers = [
        RevisionProfileTrack(self.api, track, config['profiling_pool'],
                             self.revision)
        for track, config in self.compilators.items()
    ]


class V8TryBuilder(BaseProfileBuilder):

  def __init__(self, api, compilators, change):
    super().__init__(api, compilators)
    self.change = change

  @with_wrapper_step
  def init_trackers_for_candidate_versions(self):
    self.profile_trackers = [
        ChangeProfileTrack(self.api, track, config['profiling_pool'],
                           self.change)
        for track, config in self.compilators.items()
    ]


class V8PerfTryBuilder(BaseProfileBuilder):

  def __init__(self, api, compilators, change):
    super().__init__(api, compilators)
    self.change = change

  @cached_property
  def work_dir(self):
    return self.api.path.cache_dir / 'builder'

  @with_wrapper_step
  def init_trackers_for_candidate_versions(self):
    with self.api.context(cwd=self.work_dir):
      resp = self.api.v8.git_output('ls-remote', V8_REPO_URL, 'refs/heads/lkgr')
    commit = resp.split('\t')[0]

    self.profile_trackers = [
        RevisionProfileTrack(self.api, track, config['profiling_pool'], commit)
        for track, config in self.compilators.items()
    ]

  @with_wrapper_step
  def download_benchmark_code(self):
    config = self.api.gclient.make_config()
    solution = config.solutions.add()
    solution.name = 'v8-perf'
    solution.url = V8_PERF_REPO_URL

    with self.api.context(cwd=self.work_dir):
      self.api.bot_update.ensure_checkout(gclient_config=config)

    self.perf_code_path = self.work_dir / 'v8-perf' / 'benchmarks'


class V8VersionTagBuilder(BaseProfileBuilder):

  def __init__(self, api, compilators, max_parallel_versions,
               version_number_cutoff):
    super().__init__(api, compilators)
    self.max_parallel_versions = max_parallel_versions
    self.version_number_cutoff = version_number_cutoff

  def get_version_revision(self, lines, pattern) -> set[tuple[str, str]]:
    versions_revisions = set()
    for line in lines:
      match = re.fullmatch(pattern, line)
      if not match:
        continue
      # We add the two groups in reversed order as we retrieve the revision as the
      # first group and version as the second.
      versions_revisions.add(match.groups()[::-1])
    return versions_revisions

  def select_tags_without_profiles(self) -> list[tuple[VersionTuple, str]]:
    lines = self.api.v8.git_output('ls-remote', '--tags',
                                   V8_REPO_URL).split('\n')

    all_tags = self.get_version_revision(lines, VERSION_TAG_PATTERN)
    pgo_tags = self.get_version_revision(lines, PGO_VERSION_TAG_PATTERN)

    tags_without_pgo = list(all_tags - pgo_tags)

    return [(normalize_version(version), revision)
            for version, revision in tags_without_pgo]

  def filter_tags_by_cutoff(self, tags,
                            cutoff) -> list[tuple[VersionTuple, str]]:
    return [t for t in tags if t[0] >= cutoff]

  def filter_max_parallel_tags(self, tags,
                               parallel) -> list[tuple[VersionTuple, str]]:
    return sorted(tags, reverse=True)[:parallel]

  def filter_blocked_tags(self, tags) -> list[tuple[VersionTuple, str]]:
    """Load a blocklist from a storage bucket, and remove blocked versions."""
    blocked = self.download_blocked_versions()
    self.api.step.active_result.presentation.logs[
        'blocked versions'] = self.api.json.dumps(
            blocked, indent=2)

    blocked_versions = []
    for version, justification in blocked.items():
      normalized_version = normalize_version(version)

      # Custom blocking, e.g. {"reason": "crbug.com/40245627"}.
      if "reason" in justification:
        blocked_versions.append(normalized_version)
        continue

      # Blocked due to PGO builder failures, e.g.
      # {"failures": [{"build": "<build-link>", "time": 1724865633}]}.
      retry_count = len(justification["failures"]) - 1
      if retry_count >= len(RETRY_INTERVAL):
        blocked_versions.append(normalized_version)
        continue

      first_run = justification["failures"][0]
      seconds_since = self.api.time.time() - first_run["time"]
      if RETRY_INTERVAL[retry_count] * 3600 > seconds_since:
        blocked_versions.append(normalized_version)
        continue

      if seconds_since > RETRY_TIMEOUT * 3600:
        blocked_versions.append(normalized_version)
        continue

    return [t for t in tags if t[0] not in blocked_versions]

  def upload_pgo_file(self, tracker):
    self.api.gsutil.upload(
        tracker.profile_out_file,
        BUCKET_NAME,
        tracker.remote_profile_path,
        name=f'upload {tracker.name}')

  def upload_meta_json(self, tracker_pair):
    successful_tracks = [t.track for t in tracker_pair if not t.exception]
    assert successful_tracks, 'Expected tracks, but none found to upload.'
    self.api.gsutil.upload(
        self.api.json.input({
            'version': tracker_pair[0].version,
            'revision': tracker_pair[0].revision,
            'profile': successful_tracks,
            'build_link': self.api.buildbucket.build_url(),
        }),
        BUCKET_NAME,
        f'by-version/{tracker_pair[0].version}/meta.json',
        name=f'upload metadata {tracker_pair[0].version}')

  def download_blocked_versions(self) -> dict[str, any]:
    result = self.api.gsutil.cat(
        BLOCKLIST_PATH, stdout=self.api.raw_io.output())
    blocked_lines = result.stdout.decode().strip()

    blocked_versions = defaultdict(dict)
    for line in blocked_lines.split('\n'):
      if line.startswith('#'):
        continue
      if not line:
        continue

      version, justification = line.split(maxsplit=1)
      try:
        blocked_versions[version] = self.api.json.loads(justification)
      except json.decoder.JSONDecodeError:
        blocked_versions[version] = {
            "reason": "Justification cannot be parsed",
            "original_justification": justification,
        }

    return blocked_versions

  @with_wrapper_step
  def report_exceptions(self):
    failed_versions = {t.version for t in self.failed_profile_trackers}
    if not failed_versions:
      return

    blocked = self.download_blocked_versions()
    build_failure = {
        "build": self.api.buildbucket.build_url(),
        "time": self.api.time.time(),
    }

    for version in failed_versions:
      blocked[version].setdefault('failures', []).append(build_failure)

    blockfile_lines = []
    for version, justification in blocked.items():
      blockfile_lines.append(f'{version} {self.api.json.dumps(justification)}')

    upload_content = self.api.raw_io.input_text('\n'.join([
        '# If you manually add a version to this list, use the format:',
        '# <major>.<minor>.<build>.<patch> {"reason": "<Link to CL or bug>"}',
        '#',
        '# Example:',
        '# 12.8.5.0 {"reason": "crbug.com/40245627"}',
    ] + blockfile_lines))
    self.api.gsutil.upload(
        upload_content,
        BLOCKLIST_BUCKET,
        BLOCKLIST_FILE,
        name=f'upload {BLOCKLIST_FILE}')

  @with_wrapper_step
  def init_trackers_for_candidate_versions(self):
    tags = self.select_tags_without_profiles()
    tags = self.filter_tags_by_cutoff(tags, self.version_number_cutoff)
    tags = self.filter_blocked_tags(tags)
    tags = self.filter_max_parallel_tags(tags, self.max_parallel_versions)

    tag_lines = [
        f'{".".join([str(n) for n in version])} {revision}'
        for version, revision in tags
    ]
    self.api.step.active_result.presentation.logs['filtered tags'] = tag_lines

    # TODO(b/353419839): Add the retry attempt number to the profile tracker logs.
    self.profile_trackers = []
    for version, revision in tags:
      for track, config in self.compilators.items():
        self.profile_trackers.append(
            VersionProfileTrack(self.api, track, config['profiling_pool'],
                                version, revision))

  @with_wrapper_step
  def upload_to_gs(self):
    for version_trackers in self.healthy_profile_trackers_by_version:
      if len(version_trackers) != len(self.compilators):
        continue

      for tracker in version_trackers:
        with self.exception_capture(tracker):
          self.upload_pgo_file(tracker)

      with self.exception_capture(version_trackers[0]):
        self.upload_meta_json(version_trackers)

  @with_wrapper_step
  def assign_pgo_tags(self):
    """Assign `refs/tags/<version>-pgo` to versions with all profiles."""
    for version_trackers in self.healthy_profile_trackers_by_version:
      if len(version_trackers) != len(self.compilators):
        continue

      version = version_trackers[0].version
      revision = version_trackers[0].revision

      # The git repository uses the three-component version representation
      if version.endswith('.0'):
        version = version[:-2]

      tag = f'{version}-pgo'
      self.api.gerrit.create_gerrit_tag(
          GERRIT_HOST,
          GERRIT_PROJECT,
          tag,
          revision,
          step_test_data=lambda: self.api.json.test_api.output({
              'ref': f'refs/tags/{tag}',
              'revision': revision,
              'can_delete': False,
          }))

  @with_wrapper_step
  def add_comment_to_gerrit_changes(self):
    for version_trackers in self.healthy_profile_trackers_by_version:
      if len(version_trackers) != len(self.compilators):
        continue

      commit = version_trackers[0].revision
      version = version_trackers[0].version

      change = self.api.gerrit.get_changes(
          name=f'retrieve details for {commit} ({version})',
          host=GERRIT_HOST,
          query_params=[('commit', commit)])[0]
      build_id = self.api.buildbucket.build.id
      build_url = self.api.buildbucket.build_url()

      self.api.gerrit.call_raw_api(
          name=f'add comment to {commit} ({version})',
          method='POST',
          host=GERRIT_HOST,
          path=f'/changes/{change["id"]}/revisions/1/review',
          body={
              "message":
                  "\n".join([
                      ("PGO profiles for V8 builtins have been generated in "
                       f"[build  {build_id}]({build_url}) for the following "
                       "tracks:"),
                      *[
                          f'* [{t.track}]({t.get_profile_url(BUCKET_NAME)})'
                          for t in version_trackers
                      ],
                      "",
                      ("If you suspect an error caused by PGO profiles, "
                       "[report a bug](https://bugs.chromium.org/p/chromium/issues/"
                       "entry?components=Infra>Client>V8) to the V8 Infra team."
                      ),
                  ])
          })
