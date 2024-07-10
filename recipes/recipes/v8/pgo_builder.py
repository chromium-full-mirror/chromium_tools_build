# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from collections import defaultdict
import contextlib
import itertools
import re

from recipe_engine.post_process import (
    Filter,
    DoesNotRun,
    DoesNotRunRE,
    DropExpectation,
    MustRun,
    StepCommandContains,
)
from recipe_engine.recipe_api import Property

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2

from RECIPE_MODULES.build.v8_tests import testing as v8testing
from RECIPE_MODULES.build.v8.v8version import VersionTuple, normalize_version

from google.protobuf import json_format
from google.protobuf import struct_pb2

DEPS = [
    'chromium_swarming',
    'depot_tools/gerrit',
    'depot_tools/gitiles',
    'depot_tools/gsutil',
    'depot_tools/tryserver',
    'isolate',
    'recipe_engine/buildbucket',
    'recipe_engine/cas',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'test_utils',
    'v8',
    'v8_orchestrator',
    'v8_tests',
]


# Paths relative to the V8 checkout directory
PROFILE_ONLY_PATH = 'tools/builtins-pgo/profile_only.py'
D8_OUT_PATH = 'out/build/d8'


class UnixPlatform:
  @property
  def profile_only_path(self):
    return PROFILE_ONLY_PATH

  @property
  def d8_out_path(self):
    return D8_OUT_PATH


class WindowsPlatform:
  def _to_windows_path(self, path):
    return path.replace('/', '\\')

  @property
  def profile_only_path(self):
    return self._to_windows_path(PROFILE_ONLY_PATH)

  @property
  def d8_out_path(self):
    return self._to_windows_path(f'{D8_OUT_PATH}.exe')


JET_STREAM_PATH = 'benchmarks/JetStream2'
BUCKET_NAME = 'chromium-v8-builtins-pgo'
GERRIT_HOST = 'https://chromium-review.googlesource.com'
GERRIT_PROJECT = 'v8/v8'
MAX_PARALLEL_VERSIONS = 5
V8_REPO_URL = 'https://chromium.googlesource.com/v8/v8/'
V8_PERF_REPO_URL = 'https://chrome-internal.googlesource.com/v8/v8-perf'
VERSION_CUTOFF = (11, 2)

BLOCKLIST_BUCKET = 'chromium-v8-builtins-pgo-state'
BLOCKLIST_FILE = 'blocked-versions.txt'
BLOCKLIST_PATH = f'gs://{BLOCKLIST_BUCKET}/{BLOCKLIST_FILE}'

VERSION_TAG_PATTERN = r'(\w+)\s+refs/tags/(\d+\.\d+\.\d+(?:\.\d+)?)'
PGO_VERSION_TAG_PATTERN = r'(\w+)\s+refs/tags/(\d+\.\d+\.\d+(?:\.\d+)?)-pgo'
PROFILE_PATTERN = (
    r'(block_hint,\w+(,\d+){3}\n)+'
    r'(builtin_count,\w+,\d+\n)*'
    r'(block_count,\w+(,\d+){2}\n)*'
    r'(builtin_hash,\w+,\-?\d+\n)+'
)

COMPILATORS = {
    'x86': {
      'bucket': 'ci',
      'builder': 'V8 Linux PGO instrumentation - builder',
      'platform': UnixPlatform(),
    },
    'x64': {
      'bucket': 'ci',
      'builder': 'V8 Linux64 PGO instrumentation - builder',
      'platform': UnixPlatform(),
    },
    'x86-rl': {
      'bucket': 'ci',
      'builder': 'V8 Win32 PGO instrumentation - builder',
      'platform': WindowsPlatform(),
    },
    'x64-rl': {
      'bucket': 'ci',
      'builder': 'V8 Win64 PGO instrumentation - builder',
      'platform': WindowsPlatform(),
    },
}

PROPERTIES = {
    'max_parallel_versions': Property(kind=int, default=MAX_PARALLEL_VERSIONS),
    'version_number_cutoff': Property(kind=tuple, default=VERSION_CUTOFF),
}


def RunSteps(api, max_parallel_versions, version_number_cutoff):
  work_dir = api.path.mkdtemp()
  with api.context(cwd=work_dir):
    profile_trackers = init_trackers_for_candidate_versions(
        api,
        max_parallel_versions,
        normalize_version(version_number_cutoff),
    )

    if not profile_trackers:
      return result_pb2.RawResult(status=common_pb.SUCCESS)

    orchestrator = api.v8_orchestrator.create_compilator_handler()

    trigger_compilators(api, profile_trackers, orchestrator)
    perf_code_path = download_benchmark_code(api, work_dir)
    collect_compilation_isolates(api, profile_trackers, orchestrator)
    merge_isolate_with_benchmark(api, profile_trackers, perf_code_path)
    trigger_profilers(api, profile_trackers)
    collect_profiles(api, profile_trackers)
    validate_profiles(api, profile_trackers)
    upload_to_gs(api, profile_trackers)
    assign_pgo_tags(api, profile_trackers)
    add_comment_to_gerrit_changes(api, profile_trackers)
    return report_exceptions(api, profile_trackers)


def with_wrapper_step(func):

  def wrapped_func(*args, **kwargs):
    api = args[0]
    assert hasattr(
        api, 'step'), ('Decorated function does not take recipe API object '
                       'as first argument.')

    # Check for version profile trackers as the second arg
    version_profile_trackers = None
    arg_is_version_profile_tracker = (
        len(args) > 1 and
        isinstance(args[1], list) and
        len(args[1]) > 0 and
        isinstance(args[1][0], VersionProfileTrack)
    )
    if arg_is_version_profile_tracker:
      version_profile_trackers = args[1]

    wrapper_step_name = func.__name__.replace('_', ' ')
    with api.step.nest(wrapper_step_name) as step:
      if version_profile_trackers:
        step.logs['profile trackers'] = [
            t.presentation for t in version_profile_trackers
        ]
      return func(*args, **kwargs)

  return wrapped_func


class VersionProfileTrack:
  """
  A track is the process of compiling, generating and uploading the profile
  for a single version on a track (architecture × platform). This process has
  multiple discrete steps. We want to run these steps in parallel for each
  versions and each track.

  This class manages the state of this process by collecting handlers and
  properties from every step and passes it to the next one.
  """

  def __init__(self, version, track, revision) -> None:
    self.version = '%d.%d.%d.%d' % version
    self.track = track
    self.revision = revision
    self.compilator_handler = None
    self.compilator_properties = None
    self.original_cas_digest = None
    self.profile_dir = None
    self.profile_task = None
    self.exception = None

  @property
  def profile_out_file(self):
    shard_output_dir = self.profile_task.get_task_shard_output_dirs()[0]
    return self.profile_dir / shard_output_dir / 'pgo.profile'

  def find_original_cas_digest(self, comp_props):
    self.compilator_properties = comp_props
    hashes = self.compilator_properties['swarm_hashes']
    self.original_cas_digest = hashes['d8_pgo']

  @property
  def test_spec(self):
    return self.compilator_properties['parent_test_spec']

  @property
  def swarming_dimensions(self):
    dimensions = self.test_spec['swarming_dimensions']
    dimensions.update({'pool': 'chromium.tests'})
    return dimensions

  @property
  def swarming_task_attrs(self):
    return self.test_spec['swarming_task_attrs']

  @property
  def name(self):
    return f'{self.version} {self.track}'

  @property
  def presentation(self):
    result = '❌' if self.exception else '✓'
    result += f' {self.version} {self.revision} {self.track}'

    if self.exception:
      result += f' Failure: {self.exception}'

    return result

  @property
  def remote_profile_path(self):
    return f'by-version/{self.version}/{self.track}.profile'

  @property
  def profile_url(self):
    return (f'https://storage.googleapis.com/{BUCKET_NAME}/'
            f'{self.remote_profile_path}')


@with_wrapper_step
def init_trackers_for_candidate_versions(
    api, max_parallel_versions, version_cutoff) -> list[VersionProfileTrack]:
  tags = select_tags_without_profiles(api)
  tags = filter_tags_by_cutoff(tags, version_cutoff)
  tags = filter_blocked_tags(api, tags)
  tags = filter_max_parallel_tags(tags, max_parallel_versions)
  return create_profile_trackers(tags)


def select_tags_without_profiles(api) -> list[tuple[VersionTuple, str]]:
  lines = api.v8.git_output('ls-remote', '--tags', V8_REPO_URL).split('\n')

  all_tags = get_version_revision(lines, VERSION_TAG_PATTERN)
  pgo_tags = get_version_revision(lines, PGO_VERSION_TAG_PATTERN)

  tags_without_pgo = list(all_tags - pgo_tags)

  return [
      (normalize_version(version), revision)
      for version, revision in tags_without_pgo
  ]


def get_version_revision(lines, pattern) -> set[tuple[str, str]]:
  versions_revisions = set()
  for line in lines:
    match = re.fullmatch(pattern, line)
    if not match:
      continue
    # We add the two groups in reversed order as we retrieve the revision as the
    # first group and version as the second.
    versions_revisions.add(match.groups()[::-1])
  return versions_revisions


def filter_tags_by_cutoff(tags, cutoff) -> list[tuple[VersionTuple, str]]:
  return [t for t in tags if t[0] >= cutoff]


def filter_max_parallel_tags(tags, parallel) -> list[tuple[VersionTuple, str]]:
  return sorted(tags, reverse=True)[:parallel]


def filter_blocked_tags(api, tags) -> list[tuple[VersionTuple, str]]:
  """Load a blocklist from a storage bucket, and remove blocked versions."""
  blocked = download_blocked_versions(api)
  blocked = {normalize_version(v.split()[0]) for v in blocked if v}

  return [t for t in tags if t[0] not in blocked]


def download_blocked_versions(api) -> list[str]:
  result = api.gsutil.cat(BLOCKLIST_PATH, stdout=api.raw_io.output())
  blocked = result.stdout.decode().strip()
  if not blocked:
    return []
  return blocked.split('\n')


def create_profile_trackers(selected_versions) -> list[VersionProfileTrack]:
  all_trackers = []
  for version, revision in selected_versions:
    for track in COMPILATORS:
      all_trackers.append(VersionProfileTrack(version, track, revision))
  return all_trackers


@contextlib.contextmanager
def exception_capture(api, tracker):
  try:
    yield
  except Exception as ex:
    presentation = api.step.active_result.presentation
    presentation.step_text = str(ex)
    tracker.exception = ex


@with_wrapper_step
def trigger_compilators(api, profile_trackers, orchestrator):
  for tracker in advanceable(profile_trackers):
    with exception_capture(api, tracker), api.step.nest(tracker.name):
      compilator = COMPILATORS[tracker.track]
      h = orchestrator.trigger_compilator(
          compilator['builder'], revision=tracker.revision,
          bucket=compilator['bucket'])
      tracker.compilator_handler = h


def advanceable(profile_trackers):
  return [t for t in profile_trackers if not t.exception]


def download_benchmark_code(api, work_dir):
  with api.step.nest('download benchmark code'):
    api.v8.git_output(
        'clone',
        '--depth=1',
        '--filter=blob:none',
        '--sparse',
        V8_PERF_REPO_URL,
    )
    checkout_path = work_dir / 'v8-perf'
    with api.context(cwd=checkout_path):
      api.v8.git_output(
          'sparse-checkout',
          'set',
          JET_STREAM_PATH,
      )
      return checkout_path / JET_STREAM_PATH


@with_wrapper_step
def collect_compilation_isolates(api, profile_trackers, orchestrator):
  for tracker in advanceable(profile_trackers):
    with exception_capture(api, tracker), api.step.nest(tracker.name):
      sub_build = orchestrator.launch_compilator_watcher(
          tracker.compilator_handler)
      comp_props = sub_build.output.properties['compilator_properties']
      tracker.find_original_cas_digest(comp_props)


@with_wrapper_step
def merge_isolate_with_benchmark(api, profile_trackers, perf_code_path):
  for tracker in advanceable(profile_trackers):
    with exception_capture(api, tracker), api.step.nest(tracker.name):
      cas_work_dir = api.path.mkdtemp()
      api.cas.download('download', tracker.original_cas_digest, cas_work_dir)
      api.file.copytree(
          'copy benchmark code', perf_code_path, cas_work_dir / 'JetStream2')
      tracker.augmented_cas_digest = api.cas.archive('archive', cas_work_dir)


@with_wrapper_step
def trigger_profilers(api, profile_trackers):
  # The default priority is 200 (very low). Presubmit runs at 30.
  # FYI tasks run at 40.
  api.chromium_swarming.default_priority = 25

  for tracker in advanceable(profile_trackers):
    with exception_capture(api, tracker), api.step.nest(tracker.name):
      tracker.profile_dir = api.path.mkdtemp(
          f'v{tracker.version}_{tracker.track}')

      platform = COMPILATORS[tracker.track]['platform']
      task = api.chromium_swarming.task(
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

      api.chromium_swarming.trigger_task(task)
      tracker.profile_task = task


@with_wrapper_step
def collect_profiles(api, profile_trackers):
  for tracker in advanceable(profile_trackers):
    with exception_capture(api, tracker), api.step.nest(tracker.name):
      step, is_valid = api.chromium_swarming.collect_task(tracker.profile_task)
      if step.presentation.status != api.step.SUCCESS or not is_valid:
        raise Exception(f'collect_profiles returned {step.presentation.status}')


@with_wrapper_step
def validate_profiles(api, profile_trackers):
  """Check profiles against a static structure to prevent malicious bots from
  submitting arbitrary content which might affect the build process."""

  for tracker in advanceable(profile_trackers):
    assert tracker.profile_out_file, 'Expected profile, but no file found.'

    content = api.file.read_text(
        name=f'read profile for {tracker.name}',
        source=tracker.profile_out_file,
        include_log=False)
    assert re.fullmatch(PROFILE_PATTERN, content), (
        f'Profile from tracker {tracker.name} does not match expected pattern.'
    )


@with_wrapper_step
def upload_to_gs(api, profile_trackers):
  for version_trackers in grouped_by_version(advanceable(profile_trackers)):
    if len(version_trackers) != len(COMPILATORS):
      continue

    for tracker in version_trackers:
      with exception_capture(api, tracker):
        upload_pgo_file(api, tracker)

    with exception_capture(api, version_trackers[0]):
      upload_meta_json(api, version_trackers)


def grouped_by_version(profile_trackers):
  groups = defaultdict(list)
  for t in profile_trackers:
    groups[t.version].append(t)
  return list(groups.values())


def upload_pgo_file(api, tracker):
  api.gsutil.upload(
      tracker.profile_out_file,
      BUCKET_NAME,
      tracker.remote_profile_path,
      name=f'upload {tracker.name}')


def upload_meta_json(api, tracker_pair):
  successful_tracks = [t.track for t in tracker_pair if not t.exception]
  assert successful_tracks, 'Expected tracks, but none found to upload.'
  api.gsutil.upload(
      api.json.input({
          'version': tracker_pair[0].version,
          'revision': tracker_pair[0].revision,
          'profile': successful_tracks,
          'build_link': api.buildbucket.build_url(),
      }),
      BUCKET_NAME,
      f'by-version/{tracker_pair[0].version}/meta.json',
      name=f'upload metadata {tracker_pair[0].version}')


@with_wrapper_step
def assign_pgo_tags(api, profile_trackers):
  """Assign `refs/tags/<version>-pgo` to versions with all profiles."""
  for version_trackers in grouped_by_version(advanceable(profile_trackers)):
    if len(version_trackers) != len(COMPILATORS):
      continue

    version = version_trackers[0].version
    revision = version_trackers[0].revision

    # The git repository uses the three-component version representation
    if version.endswith('.0'):
      version = version[:-2]

    tag = f'{version}-pgo'
    api.gerrit.create_gerrit_tag(
        GERRIT_HOST, GERRIT_PROJECT, tag, revision,
        step_test_data=lambda: api.json.test_api.output({
          'ref': f'refs/tags/{tag}',
          'revision': revision,
          'can_delete': False,
        })
    )

@with_wrapper_step
def add_comment_to_gerrit_changes(api, profile_trackers):
  for version_trackers in grouped_by_version(advanceable(profile_trackers)):
    if len(version_trackers) != len(COMPILATORS):
      continue

    commit = version_trackers[0].revision
    version = version_trackers[0].version

    change = api.gerrit.get_changes(
        name=f'retrieve details for {commit} ({version})',
        host=GERRIT_HOST,
        query_params=[('commit', commit)])[0]
    build_id = api.buildbucket.build.id
    build_url = api.buildbucket.build_url()

    api.gerrit.call_raw_api(
        name=f'add comment to {commit} ({version})',
        method='POST',
        host=GERRIT_HOST,
        path=f'/changes/{change["id"]}/revisions/1/review',
        body={
            "message": "\n".join([
              (
                  "PGO profiles for V8 builtins have been generated in "
                  f"[build  {build_id}]({build_url}) for the following "
                  "tracks:"
              ),
              *[f'* [{t.track}]({t.profile_url})' for t in version_trackers],
              "",
              (
                  "If you suspect an error caused by PGO profiles, "
                  "[report a bug](https://bugs.chromium.org/p/chromium/issues/"
                  "entry?components=Infra>Client>V8) to the V8 Infra team."
              ),
            ])
        })


@with_wrapper_step
def report_exceptions(api, profile_trackers):
  failed_versions = {t.version for t in profile_trackers if t.exception}
  if not failed_versions:
    return result_pb2.RawResult(status=common_pb.SUCCESS)

  update_blocked_version_file(api, failed_versions)

  return result_pb2.RawResult(
      status=common_pb.FAILURE,
      summary_markdown='Some versions encounterd exceptions')


def update_blocked_version_file(api, failed_versions):
  blocked_versions = download_blocked_versions(api)
  details = api.json.dumps({'failures': [api.buildbucket.build_url()]})

  for version in failed_versions:
    blocked_versions.append(f'{version} {details}')

  upload_content = api.raw_io.input_text('\n'.join(blocked_versions))
  api.gsutil.upload(
      upload_content, BLOCKLIST_BUCKET, BLOCKLIST_FILE,
      name=f'upload {BLOCKLIST_FILE}')


def GenTests(api):
  all_tracks = {'x86', 'x64', 'x86-rl', 'x64-rl'}

  def stdout(step_name, text):
    return api.override_step_data(
        step_name, api.raw_io.stream_output_text(text, stream='stdout'))

  def stdout_binary(step_name, content):
    return api.override_step_data(
        step_name, api.raw_io.stream_output(content, stream='stdout'))

  def mock_compilation(versions, archs, compilator_properties=None):
    if compilator_properties is None:
      compilator_properties = {
          'swarm_hashes': {
              'd8_pgo': 'fa3e4a54'
          },
          'parent_test_spec': {
              'tests': [['pgo_instrumentation']],
              'swarming_dimensions': {
                  'os': 'Ubuntu 31.41'
              },
              'swarming_task_attrs': {
                  'expiration': 21600,
                  'hard_timeout': 7200,
              }
          },
      }

    sub_build = build_pb2.Build(
        id=54321,
        status=common_pb.SUCCESS,
        summary_markdown='All good!',
        output=dict(
            properties=json_format.Parse(
                api.json.dumps({
                    'compilator_properties': compilator_properties
                }), struct_pb2.Struct())))

    return [
        api.step_data(
            f'collect compilation isolates.{version} {arch}.compilator steps',
            api.step.sub_build(sub_build))
        for version, arch in itertools.product(versions, archs)
    ]

  def mock_profiles(versions, archs, content=None):
    mock_content = content or (
        'block_hint,CallUndefinedReceiver1ExtraWideHandler,19,20,0\n'
        'builtin_count,ShiftLeftWideHandler,0\n'
        'block_count,MulSmiExtraWideHandler,34,0\n'
        'builtin_hash,RecordWriteSaveFP,234037449\n'
    )
    return [
        api.step_data(
            f'validate profiles.read profile for {version} {arch}',
            api.file.read_text(mock_content))
        for version, arch in itertools.product(versions, archs)
    ]


  def main_scenario(name, *args, **kwargs):
    return api.test(
        name,
        api.properties(max_parallel_versions=2, version_number_cutoff=(1, 1)),
        stdout(
            'init trackers for candidate versions.git ls-remote', '\n'.join([
                '1234 refs/tags/0.1.10.1',
                '2234 refs/tags/1.1.1',
                '2234 refs/tags/1.1.1-pgo',
                'ab34 refs/tags/1.1.1.1',
                'ab34 refs/tags/1.1.1.1-pgo',
                'abde refs/tags/1.1.1.2',
                'f034 refs/tags/1.1.1.3',
                'cd12 refs/tags/1.1.1.4',
                '43ff refs/tags/1.1.2',
                '',
            ])),
        *args, **kwargs
    )

  yield main_scenario(
      'basic',
      *mock_compilation(['1.1.2.0', '1.1.1.4'], all_tracks),
      *mock_profiles(['1.1.2.0', '1.1.1.4'], all_tracks),
  )

  yield api.test(
      'no_new_versions',
      stdout(
          'init trackers for candidate versions.git ls-remote', '\n'.join([
              '2234 refs/tags/1.1.1',
              '2234 refs/tags/1.1.1-pgo',
              '',
          ])),
      api.post_process(
          DoesNotRunRE,
          'augment isolates.*',
          'trigger profilers.*',
          'collect profiles.*',
          'upload to gs.*',
          'assign pgo tags.*',
      ),
      api.post_process(DropExpectation),
  )

  failing_track = 'x86'
  passing_tracks = all_tracks - {failing_track}
  yield main_scenario(
      'one_track_compilation_failure',
      *mock_compilation(['1.1.2.0'], all_tracks),
      *mock_profiles(['1.1.2.0'], all_tracks),
      *mock_compilation(['1.1.1.4'], passing_tracks),
      *mock_profiles(['1.1.1.4'], passing_tracks),
      *mock_compilation(['1.1.1.4'], [failing_track], compilator_properties={}),
      api.post_process(
          DoesNotRun,
          f'augment isolates.1.1.1.4 {failing_track}',
          f'trigger profilers.1.1.1.4 {failing_track}',
          f'collect profiles.1.1.1.4 {failing_track}',
          f'upload to gs.gsutil upload 1.1.1.4 {failing_track}',
          'upload to gs.gsutil upload metadata 1.1.1.4',
          'assign pgo tags.gerrit create_gerrit_tag (v8/v8 1.1.1.4-pgo)',
      ),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  failing_track = 'x86'
  passing_tracks = all_tracks - {failing_track}
  yield main_scenario(
      'one_track_profiling_failure',
      *mock_compilation(['1.1.2.0', '1.1.1.4'], all_tracks),
      *mock_profiles(['1.1.2.0'], all_tracks),
      *mock_profiles(['1.1.1.4'], passing_tracks),
      api.step_data(
          f'collect profiles.1.1.1.4 {failing_track}.pgo profile 1.1.1.4 x86 '
          'on Ubuntu 31.41',
          api.chromium_swarming.summary(
              api.test_utils.canned_gtest_output(True),
              {'shards': [{
                  'state': 'FAILURE'
              }]})),
      api.post_process(
          DoesNotRun,
          f'upload to gs.gsutil upload 1.1.1.4 {failing_track}',
          'upload to gs.gsutil upload metadata 1.1.1.4',
          'assign pgo tags.gerrit create_gerrit_tag (v8/v8 1.1.1.4-pgo)',
      ),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield main_scenario(
      'full_version_failure',
      *mock_compilation(['1.1.2.0'], all_tracks),
      *mock_profiles(['1.1.2.0'], all_tracks),
      *mock_compilation(['1.1.1.4'], all_tracks, compilator_properties={}),
      api.post_process(
          DoesNotRun,
          'augment isolates.1.1.1.4 x86',
          'trigger profilers.1.1.1.4 x86',
          'collect profiles.1.1.1.4 x86',
          'upload to gs.gsutil upload 1.1.1.4 x86',
          'augment isolates.1.1.1.4 x64',
          'trigger profilers.1.1.1.4 x64',
          'collect profiles.1.1.1.4 x64',
          'upload to gs.gsutil upload 1.1.1.4 x64',
          'upload to gs.gsutil upload metadata 1.1.1.4',
          'assign pgo tags.gerrit create_gerrit_tag (v8/v8 1.1.1.4-pgo)',
      ),
      api.post_process(
          StepCommandContains,
          'report exceptions.gsutil upload blocked-versions.txt', [
              '1.1.1.4 {"failures": ["https://cr-buildbucket.appspot.com/build/0"]}'
          ]),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'version_number_cutoff',
      api.properties(
          max_parallel_versions=100, version_number_cutoff=(1, 1, 1)),
      stdout(
          'init trackers for candidate versions.git ls-remote',
          '1234 refs/tags/0.1.10.1\n'
          '1234 refs/tags/1.0.3.1\n'
          '2234 refs/tags/1.1.1\n'
          'ab34 refs/tags/1.1.1.1\n'
          'abde refs/tags/1.1.1.2\n'
          'f034 refs/tags/1.1.1.3\n'
          'cd12 refs/tags/1.1.1.4\n'),
      api.post_process(
          MustRun,
          'trigger compilators.1.1.1.4 x86',
          'trigger compilators.1.1.1.4 x64',
          'trigger compilators.1.1.1.3 x86',
          'trigger compilators.1.1.1.3 x64',
          'trigger compilators.1.1.1.2 x86',
          'trigger compilators.1.1.1.2 x64',
          'trigger compilators.1.1.1.1 x86',
          'trigger compilators.1.1.1.1 x64',
          'trigger compilators.1.1.1.0 x86',
          'trigger compilators.1.1.1.0 x64',
      ),
      api.post_process(
          DoesNotRun,
          'trigger compilators.1.0.3.1 x86',
          'trigger compilators.1.0.3.1 x64',
          'trigger compilators.0.1.1.10 x86',
          'trigger compilators.0.1.1.10 x64',
      ),
      api.post_process(DropExpectation),
      # The test fails due to missing mock data for following steps.
      status='FAILURE',
  )

  yield api.test(
      'version_blocklist',
      api.properties(
          max_parallel_versions=100, version_number_cutoff=(1, 1, 1)),
      stdout(
          'init trackers for candidate versions.git ls-remote',
          'ab34 refs/tags/1.1.1.1\n'
          'abde refs/tags/1.1.1.2\n'),
      stdout_binary(
          'init trackers for candidate versions.gsutil cat',
          '1.1.1.2 {"failures": ["url1", "url2"]}\n'
          '1.1.1.4 {"failures": ["url3", "url4"]}'),
      api.post_process(
          MustRun,
          'trigger compilators.1.1.1.1 x86',
          'trigger compilators.1.1.1.1 x64',
      ),
      api.post_process(
          DoesNotRun,
          'trigger compilators.1.1.1.2 x86',
          'trigger compilators.1.1.1.2 x64',
      ),
      api.post_process(DropExpectation),
      # The test fails due to missing mock data for following steps.
      status='FAILURE',
  )

  yield main_scenario(
      'tampered_profile',
      *mock_compilation(['1.1.2.0', '1.1.1.4'], ['x86', 'x64']),
      *mock_profiles(['1.1.2.0', '1.1.1.4'], ['x86']),
      *mock_profiles(['1.1.2.0'], ['x64']),
      *mock_profiles(['1.1.1.4'], ['x64'], content='invalid-content'),
      api.expect_exception('AssertionError'),
      api.post_process(DropExpectation),
  )
