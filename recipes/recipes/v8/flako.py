# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Recipe to bisect flaky tests in V8.

The recipe offers a regression, a progression and a repro-only mode.

Regression bisection will start at a known bad to_revision and:
1. Calibrate the number of repetitions until enough confidence is reached.
2. Bisect backwards exponentially, doubling the offset in each step.
3. After finding a good from_revision, bisect into the range
   from_revision..to_revision and report the suspect.

Progression bisection will start at a known bad to_revision and:
1. Calibrate the number of repetitions until enough confidence is reached.
2. Check if ToT is still flaky.
3. Bisect into the range to_revision..ToT and report the commit suspected
   to fix the problem.

Repro-only mode runs aborts if the flake still reproduces at to_revision.

Tests are only run on existing isolated files, looked up on Google Storage.

All revisions during bisections are represented as offsets to the start revision
which has offset 0.

See PROPERTIES for documentation on the recipe's interface.
"""

import json
import re

from recipe_engine.config import Single
from recipe_engine.post_process import (DoesNotRun, DropExpectation, Filter,
                                        MustRun, SummaryMarkdown,
                                        SummaryMarkdownRE)
from recipe_engine.recipe_api import Property

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine.result import RawResult


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_swarming
from RECIPE_MODULES.depot_tools import gitiles, gsutil
from RECIPE_MODULES.recipe_engine import (
    json as json_module,
    path,
    properties,
    raw_io,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium_swarming: chromium_swarming.API
  gitiles: gitiles.API
  gsutil: gsutil.API
  json: json_module.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_swarming: chromium_swarming.TEST_API
  json: json_module.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  step: step.TEST_API

PROPERTIES = {
    # Group of the builder that produced the builds for bisection.
    'bisect_builder_group': Property(kind=str),
    # Name of the builder that produced the builds for bisection.
    'bisect_buildername': Property(kind=str),
    # Extra arguments to V8's run-tests.py script.
    'extra_args': Property(default=None, kind=list),
    # Regular expression that must match in the output of failing tests.
    'failure_regexp': Property(default=None, kind=str),
    # The maximum number of calibration attempts (safeguard to prevent infinite
    # loops). Repetitions are doubled on each attempt until there's enough
    # confidence.
    'max_calibration_attempts': Property(default=5, kind=Single((int, float))),
    # Minimum number of flakes needed to have confidence in a run.
    'min_flake_threshold': Property(default=4, kind=Single((int, float))),
    # Name of the isolated file (e.g. bot_default, mjsunit).
    'isolated_name': Property(kind=str),
    # Bisection mode: one of {regression|progression|combined|repro}.
    # Mode regression bisects backwards to determine what introduced a flake.
    # Mode progression bisects forwards to determine what fixed a flake.
    # Mode combined runs progression and regression bisection.
    # Mode repro only checks if a flake reproduces with the given revision.
    'mode': Property(default='combined', kind=str),
    # Initial number of swarming shards.
    'num_shards': Property(default=2, kind=Single((int, float))),
    # Optional build directory for backwards-compatibility, e.g. 'out/Release'.
    'outdir': Property(default='out/build', kind=str),
    # List of tuples (commit position, old flag, new flag regexp).
    # Can be used to override the recipe-configured list of flags that got
    # renamed or are new from some commit position onwards.
    'override_flag_history': Property(default=None, kind=list),
    # Initial number of test repetitions (passed to --random-seed-stress-count
    # option).
    'repetitions': Property(default=5000, kind=Single((int, float))),
    # Revision known to be bad, where bisection will start. ToT by default.
    'revision': Property(default=None, kind=str),
    # Swarming dimensions classifying the type of bot the tests should run on.
    # Passed as list of strings, each in the format name:value.
    'swarming_dimensions': Property(default=None, kind=list),
    # Swarming priority to be used for swarming tasks. The priority is lowered
    # by 10 (actual numberic value increases) when using higher time than the
    # one specified in the total_timeout_sec.
    'swarming_priority': Property(default=25, kind=Single((int, float))),
    # Expiration used for swarming tasks. Counted from the moment task is
    # scheduled.
    'swarming_expiration': Property(default=60 * 60, kind=Single((int, float))),
    # Fully qualified test name passed to run-tests.py. E.g. mjsunit/foobar.
    'test_name': Property(kind=str),
    # Timeout parameter passed to run-tests.py. Keep small when bisecting
    # fast-running tests that occasionally hang.
    'timeout_sec': Property(default=60, kind=Single((int, float))),
    # Initial total timeout for one entire bisect step. During calibration, this
    # time might be increased for more confidence. Set to 0 to disable and
    # specify the 'repetitions' property instead.
    'total_timeout_sec': Property(default=120, kind=Single((int, float))),
    # TODO(machenbach): Deprecated. Use "revision". Remove after 2022/10/15.
    'to_revision': Property(default=None, kind=str),
    # Name of the testing variant passed to run-tests.py.
    'variant': Property(kind=str),
}

# The maximum number of steps for backwards and inwards bisection (safeguard to
# prevent infinite loops).
MAX_BISECT_STEPS = 16

# A build with cas_digests must be within a distance of maximum 32 revisions for
# any revision that should be tested. We don't look further as a safeguard.
MAX_CAS_OFFSET = 32

# Maximum number of revisions between known_bad and refs/heads/main.
MAX_HEAD_OFFSET = 2000

# Maximum number of test name characters printed in UI in step names.
MAX_LABEL_SIZE = 32

# Maximum number of swarming shards to be used for a single attempt.
MAX_SWARMING_SHARDS = 8

# Response of gsutil when non-existing objects are looked up.
GSUTIL_NO_MATCH_TXT = 'One or more URLs matched no objects'

# URL of V8 repository.
REPO = 'https://chromium.googlesource.com/v8/v8'

# Exit code of V8's run-tests.py when no tests were run.
EXIT_CODE_NO_TESTS = 2

# Output of V8's test runner when all tests passed.
TEST_PASSED_TEXT = """
=== All tests succeeded
""".strip()

# Output of V8's test runner when some tests failed.
TEST_FAILED_TEMPLATE = """
=== %d tests failed
""".strip()

# Configuration of the flag history.
# Tuples of (commit position of change, old flag, new flag regexp).
# For older revisions prior to commit position, flags that match the new-flag
# regexp will either be replaced with the old flag or be removed if the old
# flag is None.
#
# Example:
# [
#    (100, '--foo', '--bar'),
#    (110, None, '--baz'),
#    (120, None, '--bar=-*'),
# ]
# For revisions prior to 100, --bar will be replaced with --foo.
# For revisions prior to 110, --baz will be removed.
# For revisions prior to 120, flags matching --bar=-*, e.g. --bar=10 will be
# removed.
FLAG_HISTORY = [
    (97513, None, r'--framework=.*'),
]


class Command:
  """Helper class representing a command line to V8's run-tests.py."""
  def __init__(self, outdir, test_name, variant, repetitions, repro_only,
               total_timeout_sec, timeout, extra_args, flag_history,
               min_flake_threshold):
    self.repetitions = repetitions
    self.test_name = test_name
    self.total_timeout_sec = total_timeout_sec
    self.min_failures = 1 if repro_only else min_flake_threshold
    self.flag_history = flag_history
    self.base_cmd = [
        'tools/run-tests.py',
        '--progress=verbose',
        f'--outdir={outdir}',
        f'--timeout={timeout}',
        '--swarming',
        f'--variants={variant}',
        f'--exit-after-n-failures={self.min_failures}',
    ]
    if repro_only:
      # In repro-only mode we keep running skipped tests.
      self.base_cmd.append('--run-skipped')
    self.base_cmd += (extra_args or [])
    self.base_cmd.append(test_name)

  @property
  def label(self):
    """Test name for UI output limited to MAX_LABEL_SIZE chars."""
    if len(self.test_name) > MAX_LABEL_SIZE:
      return self.test_name[:MAX_LABEL_SIZE - 3] + '...'
    return self.test_name

  def rewrite_command(self, cmd, commit_position):
    """Replace flags in the command line with older versions or drop them
    entirely if no older version exists.

    This does not rewrite the command at offset 0, for which we do not have a
    commit position, since that is fetched lazily.
    """
    if not commit_position:
      return cmd

    # Loop over all replacements, but bail out early if we are in a newer
    # revision.
    result_command = cmd
    for historical_position, old_flag, new_flag_re in self.flag_history:
      if historical_position <= commit_position:
        continue
      command_with_replacement = []

      for flag in result_command:
        if not re.match(new_flag_re, flag):
          command_with_replacement.append(flag)
          continue

        if old_flag is None:
          continue

        command_with_replacement.append(old_flag)
      result_command = command_with_replacement
    return result_command

  def raw_cmd(self, multiplier, commit_position):
    cmd = list(self.base_cmd)
    cmd = self.rewrite_command(cmd, commit_position)
    if self.total_timeout_sec:
      cmd.append('--random-seed-stress-count=1000000')
      cmd.append(f'--total-timeout-sec={self.total_timeout_sec * multiplier}')
    else:
      cmd.append(f'--random-seed-stress-count={self.repetitions * multiplier}')
    return ['python3', '-u'] + cmd


class ReturnException(Exception):

  def __init__(self, message):
    super().__init__()
    self.message = message


def raw_gs_url_template(builder_group, buildername):
  return f'gs://chromium-v8/isolated/{builder_group}/{buildername}/%s.json'

def fallback_buildername(buildername):
  """V8-side logic for deducing the name of a builder from its compiling
  builder name, following current naming conventions.

  Foobar - builder -> Foobar.
  Foobar - debug builder -> Foobar - debug.
  Foobar - anything -> Foobar - anything.

  The last entry is to simplify code for cases where no compiling builder
  exists.
  """
  if buildername.endswith(' - builder'):
    return buildername[:-len(' - builder')]
  if buildername.endswith(' builder'):
    return buildername[:-len(' builder')]
  return buildername


class Depot:
  """Helper class for mapping offsets to git revisions."""

  def __init__(self, api, known_bad_revision):
    """
    Args:
      known_bad_revision: Start revision of bisection. All other revisions
          during bisection will be represented as offsets to this revision.
    """
    self.api = api
    self.commit_position_zero = None
    self.revisions = {}

    if known_bad_revision:
      # Cache for the offset of refs/heads/main.
      self.head_offset = None
      self.known_bad_revision = known_bad_revision
      self.revisions[0] = known_bad_revision
    else:
      # If the script was started without a revision, initialize it to ToT.
      self.head_offset = 0
      commits = self._fetch_commits(
          'init head', 'refs/heads/main', 0)
      self._update_caches(commits, 0)
      self.known_bad_revision = self.revisions[0]

  def _parse_commit_position(self, value):
    """Returns (ref, revision_number) tuple."""
    RE_COMMIT_POSITION = re.compile(
        r'Cr-Commit-Position: (?P<ref>refs/[^@]+)@{#(?P<revision>\d+)}')
    matches = [
        match.groupdict() for match in RE_COMMIT_POSITION.finditer(value)
    ]
    if not matches:
      raise ValueError(f'Commit position "{value}" does not match '
                       f'r"{RE_COMMIT_POSITION.pattern}"')
    return int(matches[len(matches) - 1]['revision'])

  def _update_caches(self, commits, offset):
    """Updates the revision cache and commit-position offset.

    This updates self.revisions mapping all fetched revisions relative to
    the offset of the first revision in the list. All offsets are relative
    to the known bad revision at offset 0.

    This also caches the commit-position offset self.commit_position_zero.
    All other commit positions are relative to the cached one.
    """
    for i, commit in enumerate(commits):
      self.revisions[offset + i] = commit['commit']

    if self.commit_position_zero is None:
      commit_position = self._parse_commit_position(commits[0]['message'])
      # Note, negative offsets refer to commits in the future. Example:
      # known bad at offset: 0 and cp: 100, commits[0] at offset -10, cp: 110
      # results in commit_position_zero = 110 + (-10) = 100.
      self.commit_position_zero = commit_position + offset

  def _fetch_commits(self, step_name, git_ref, offset):
    """Fetches commits via gitiles REST api.

    Commits are fetched using a backwards counter.

    Args:
      step_name: Name of the step, also for referencing it in test data.
      git_ref: Ref or git hash that is a descendant of the commits we want
          to fetch.
      offset: Counter at how many parent commits behind git_ref we want to
          start fetching commits.
    """
    commits, _ = self.api.gitiles.log(
        REPO, f'{git_ref}~{offset}',
        limit=1,
        step_name=f'{step_name} #{offset}',
    )
    assert commits
    return commits

  def get_commit_position(self, offset):
    if not self.commit_position_zero:
      return None
    return self.commit_position_zero - offset

  def _guard_large_offset(self, fetch_offset):
    if fetch_offset >= MAX_HEAD_OFFSET:
      raise self.api.step.StepFailure(
          f'Could not connect the known bad revision to refs/heads/main. '
          f'Looked in over {MAX_HEAD_OFFSET} commits.')

  def get_head_offset(self):
    """Returns the offset from the known bad revision to the revision at
    refs/heads/main, which is set to a fixed revision when retrieved the
    first time.

    Initially, we fetch commits starting at main and iterate until we find
    the known bad revision. Once found, we store the offset and all fetched
    commits.

    Example iteration with head revision A and known bad revision F:
    - fetch A, B, C at fetch_offset 0
    - fetch D, E, F at fetch_offset 3
    - relative index of F is 2
    - head_offset is -2 - 3 = -5
    """
    all_commits = []
    head_revision = None
    while self.head_offset is None:
      fetch_offset = len(all_commits)
      self._guard_large_offset(fetch_offset)
      fetched_commits = self._fetch_commits(
          'init head',
          head_revision or 'refs/heads/main',
          fetch_offset,
      )
      all_commits.extend(fetched_commits)

      # Cache the git hash of refs/heads/main and use it further on to keep
      # bisection stable - refs are volatile.
      head_revision = head_revision or all_commits[0]['commit']

      # Check if we find the known bad revision in the list of head
      # revisions. If we do, the index is the negated offset.
      for i, c in enumerate(fetched_commits):
        if self.known_bad_revision == c['commit']:
          self.head_offset = -i - fetch_offset
          break

    # Cache what was fetched relative to the known bad revision at 0.
    self._update_caches(all_commits, self.head_offset)

    return self.head_offset

  def get_revision(self, offset):
    """Returns the git revision at the given offset (cached)."""
    revision = self.revisions.get(offset)
    if not revision:
      # We assume positive offsets as all negative offsets are already fetched
      # when initializing known_bad_revision..refs/heads/main.
      assert offset >= 0
      commits = self._fetch_commits(
          'get revision', self.known_bad_revision, offset)
      self._update_caches(commits, offset)

    return self.revisions[offset]


class Builds:
  """Helper class for locating builds in content-addressed storage (CAS)."""

  def __init__(self, api, depot, builder_group, buildername, isolated_name):
    """
    Args:
      depot: Helper that maps offsets to revisions.
      builder_group: Group name of the builder that produced the builds for
          bisection.
      buildername: Name of the builder that produced the builds for bisection.
      isolated_name: Name of the isolated file (e.g. bot_default, mjsunit).
    """
    self.api = api
    self.depot = depot
    self.isolated_name = isolated_name
    # Two templates for looking up the builder before and after a potential
    # split into builder/tester.
    self.gs_url_template_first = raw_gs_url_template(
        builder_group, buildername)
    self.gs_url_template_second = raw_gs_url_template(
        builder_group, fallback_buildername(buildername))
    # Cache for offsets that exist at fallback build location.
    self.fallback_offsets = set()
    # Cache for cas digests.
    self.cas_digests = {}
    # Offset cache for closest builds with cas digests.
    self.closest_builds = {}

  def _gs_url(self, offset):
    """Chooses the gs_url that points to either the original or a fallback
    builder in case of a builder split.
    """
    rev = self.depot.get_revision(offset)
    if offset not in self.fallback_offsets:
      return self.gs_url_template_first % rev
    return self.gs_url_template_second % rev

  @property
  def fallback_available(self):
    """A fallback builder is available if the url templates differ. Otherwise
    they point to the same builder.
    """
    return self.gs_url_template_first != self.gs_url_template_second

  def _lookup_build_test_data(self):
    """By default all lookups fail (i.e. no builds available). Override
    testdata in specific test cases to make lookups pass.
    """
    return self.api.raw_io.test_api.stream_output_text(
        GSUTIL_NO_MATCH_TXT,
        stream='stderr',
        retcode=1,
    )

  def _lookup_build(self, gs_url_template, name_suffix, offset):
    rev = self.depot.get_revision(offset)
    link = f'{REPO}/+/{rev}'
    try:
      self.api.gsutil.list(
          gs_url_template % rev,
          name=f'lookup cas_digests for #{offset}{name_suffix}',
          stderr=self.api.raw_io.output_text(),
          step_test_data=self._lookup_build_test_data,
      )
      return True
    except self.api.step.StepFailure as e:
      # Gsutil's api has no good result format for missing objects, hence, we
      # look for the output text for missing objects. Treat missing object as
      # success as we expect some builds not to exist.
      if GSUTIL_NO_MATCH_TXT in e.result.stderr:
        e.result.presentation.status = self.api.step.SUCCESS
        return False
      raise  # pragma: no cover
    finally:
      self.api.step.active_result.presentation.links[rev[:8]] = link

  def has_build(self, offset):
    """Checks if a cas digest exists for the given offset.

    Tries the original requested and a fallback builder if one exists. The
    "fallback_offsets" will indicate if the fallback builder should be used
    at this offset, when downloading the build later.
    """
    result = self._lookup_build(
        self.gs_url_template_first, '', offset)
    if not result and self.fallback_available:
      # Indicate that this build could potentially be found using the fallback.
      self.fallback_offsets.add(offset)
      result = self._lookup_build(
          self.gs_url_template_second, ' (fallback)', offset)
    return result

  def find_closest_build(self, offset, max_offset=None):
    """Looks backwards for the closest offset with an existing digest (cached).

    Args:
      offset: The offset to the known_bad_revision where the lookup is started.
      max_offset: Lookup stops at this offset if reached.
    Returns:
      The closest offset for which a cas digest exists.
    """
    closest = self.closest_builds.get(offset)
    if closest is not None:
      return closest
    for i in range(MAX_CAS_OFFSET):
      closest = offset + i
      if closest == max_offset or self.has_build(closest):
        for j in range(offset, closest + 1):
          # Cache the closest build for all offsets we tried.
          self.closest_builds[j] = closest
        return closest
    raise self.api.step.StepFailure('Couldn\'t find cas_digests.')

  def get_cas_digest(self, offset):
    """Returns the cas digest for a given offset (cached)."""
    if offset in self.cas_digests:
      return self.cas_digests[offset]

    self.api.gsutil.download_url(
        self._gs_url(offset),
        self.api.json.output(),
        name=f'get cas_digests for #{offset}',
        step_test_data=lambda: self.api.json.test_api.output(
            {'foo_isolated': '[dummy hash for foo_isolated]/123'}),
    )
    step_result = self.api.step.active_result
    self.cas_digests[offset] = step_result.json.output[self.isolated_name]
    assert '/' in self.cas_digests[offset]
    return self.cas_digests[offset]


class Runner:
  """Helper class for executing the V8 test runner to check for flakes."""
  def __init__(self, api, builds, command, num_shards, repro_only,
               max_calibration_attempts, min_flake_threshold, failure_regexp):
    self.api = api
    self.builds = builds
    self.command = command
    self.num_shards = min(num_shards, MAX_SWARMING_SHARDS)
    self.repro_only = repro_only
    self.multiplier = 1
    self.max_calibration_attempts = max_calibration_attempts
    self.min_flake_threshold = min_flake_threshold
    self.failure_regexp = None
    if failure_regexp:
      self.failure_regexp = re.compile(failure_regexp)

  def calibrate(self, offset):
    """Calibrates the multiplier for test time or repetitions of the runner for
    the given offset.

    Testing is repeated until min_flake_threshold test failures are counted in
    an attempt. First the number of swarming shards, then the multiplier is
    doubled on each fresh attempt.

    Args:
      offset (int): Distance to the start commit.
    """
    for i in range(self.max_calibration_attempts):
      # Nest to disambiguate step names during calibration.
      with self.api.step.nest(f'calibration attempt {i + 1}') as parent:
        num_failures = self.check_num_flakes(offset)
        if (self.repro_only and num_failures or
            num_failures >= self.min_flake_threshold):
          parent.step_text = 'successfully reproduced flaky test'
          return True
        if self.num_shards < MAX_SWARMING_SHARDS:
          # First double the swarming shards until reaching the maximum.
          self.num_shards = min(self.num_shards * 2, MAX_SWARMING_SHARDS)
        else:
          self.multiplier *= 2
        if i == self.max_calibration_attempts - 1:
          parent.step_text = 'failed to reproduce the flaky test'
    return False

  def _default_task_pass_test_data(self):
    test_data = self.api.chromium_swarming.test_api.canned_summary_output_raw()
    test_data['shards'][0]['output'] = TEST_PASSED_TEXT
    test_data['shards'][0]['exit_code'] = 0
    return (
        self.api.chromium_swarming.test_api.summary(
            self.api.json.test_api.output({}) +
            self.api.raw_io.test_api.output(''),
            test_data)
    )

  def num_failures(self, step_result):
    """Determine the number of failures from the results of one swarming shard.
    """
    data = step_result.chromium_swarming.summary['shards'][0]
    assert data.get('state') == 'COMPLETED', (
        'The bot might have died. Please restart the analysis')
    if data.get('exit_code', 0) == EXIT_CODE_NO_TESTS:
      # The desired test doesn't exist in this revision. This counts
      # as good as no test means no flaky test.
      return 0  # pragma: no cover

    output = data.get('output')

    # TODO(machenbach): Add this information to the V8 test runner's json
    # output as parsing stdout is brittle.
    if TEST_PASSED_TEXT in output:
      return 0

    # TODO(https://crbug.com/v8/13011): For now we only verify one overall
    # match of this regular expression and afterwards count all the test
    # failures. We might still count test failures where the expression didn't
    # match. This can be fixed by using V8's json test output instead.
    if self.failure_regexp and not self.failure_regexp.search(output):
      return 0

    no_cas = re.search(r'\w+\/\d+ not found in the CAS', output)
    if no_cas:
      raise ReturnException(
          'Bisect goes too far back. Builds went out of retention.')
    match = re.search(r'=== (\d+) tests failed', output)
    assert match
    return int(match.group(1))

  def check_num_flakes(self, offset):
    """Stress tests the given revision and returns the number of failures.

    Returns: Boolean indicating if enough failures have been found.
    """
    # TODO(machenbach): Use the sharding logic from the swarming module. We
    # don't use it yet, since swarming sets the GTEST_SHARD_INDEX environment
    # variable, which is used by the V8 test runner. This makes the test
    # disappear on all but one shards, because the test runner distributes tests
    # in a way such that each test only runs on one shard.
    # We first need a change of that logic on V8-side to suppress using
    # GTEST_SHARD_INDEX for flake bisection (e.g. by introducing another flag).
    # This V8-side commit needs to age enough before using it on infra-side,
    # so that it is availabe in each revision when bisecting backwards.

    cas_digest = self.builds.get_cas_digest(offset)
    step_prefix = f'check {self.command.label} at #{offset}'
    commit_position = self.builds.depot.get_commit_position(offset)

    def trigger_task(path, shard):
      # TODO(machenbach): Would be nice to just use 'shard X' as step names for
      # trigger/collect. But swarming enforces unique task titles and we can't
      # use our optimization to not collect some tasks. Either properly
      # cancel the task, such that they are not in the list of pending tasks or
      # override the step names.
      task = self.api.chromium_swarming.task(
          name=f'{step_prefix} - shard {shard}',
          task_output_dir=path / f'task_output_dir_{shard}',
          raw_cmd=self.command.raw_cmd(self.multiplier, commit_position),
          cas_input_root=cas_digest,
      )

      # Use lower swarming priority given the increased time of the task.
      if self.multiplier > 1:
        task.request = (task.request.with_priority(
                          max(task.request.priority + 10, 255)))

      # Override cpu defaults for Android as such devices don't have this
      # dimension.
      task_slice = task.request[0]
      task_dimensions = task_slice.dimensions
      if task_dimensions['os'] == 'Android':
        task_dimensions['cpu'] = None

      task_slice = task_slice.with_dimensions(**task_dimensions)
      task.request = task.request.with_slice(0, task_slice)

      self.api.chromium_swarming.trigger_task(task)
      return task

    def collect_task(task):
      step_result, _ = self.api.chromium_swarming.collect_task(
          task, step_test_data=self._default_task_pass_test_data)
      return self.num_failures(step_result)

    # TODO(sergiyb): Make bisect more robust to infra failures, e.g. we trigger
    # several dozen of tasks during bisect and currently if one expires, the
    # whole thing goes purple.
    path = self.api.path.mkdtemp('v8-flake-bisect-')
    with self.api.step.nest(step_prefix) as parent:
      tasks = [
        trigger_task(path, shard)
        for shard in range(self.num_shards)
      ]
      num_failures = 0
      for task in tasks:
        num_failures += collect_task(task)
        if (self.repro_only and num_failures or
            num_failures >= self.min_flake_threshold):
          # Stop waiting for more tasks early if already enough failures are
          # found.
          # TODO(machenbach): Cancel the tasks we don't collect. During
          # calibration we might even want to figure out a better number of
          # shards? E.g. when doubling from 4 to 8, maybe 5 was enough and
          # should be used throughout.
          break
      parent.step_text = f'{num_failures} failures'
      return num_failures


class Validator:

  def __init__(self, api):
    self.api = api

  def validate(self, known_bad_reproduces):
    if not known_bad_reproduces:
      raise self.api.step.StepFailure('Could not reach enough confidence.')


class Bisector(Validator):
  def __init__(self, api, depot, builds, is_bad_func):
    """Collection of bisection helpers.

    Args:
      api: Recipe api.
      depot: Helper for accessing storage and git.
      is_bad_func: Function (revision->bool) determining if a revision is bad.
    """
    super().__init__(api)
    self.depot = depot
    self.builds = builds
    self.is_bad_func = is_bad_func

  def report_range(self, text, from_offset, to_offset):
    from_revision = self.depot.get_revision(from_offset)
    to_revision = self.depot.get_revision(to_offset)
    offset_range = f'#{from_offset}..#{to_offset}'
    full_git_range = f'{from_revision}..{to_revision}'
    git_range = f'{from_revision[:8]}..{to_revision[:8]}'
    step_text = text % offset_range
    range_link = f'{REPO}/+log/{full_git_range}'
    step_result = self.api.step(step_text, cmd=None)
    step_result.presentation.links[git_range] = range_link
    return text % f'[{offset_range}]({range_link})'

  def report_revision(self, text, offset):
    rev = self.depot.get_revision(offset)
    rev_cp = self.depot.get_commit_position(offset)
    step_result = self.api.step(
        text % f'#{offset} (commit position: {rev_cp})', cmd=None)
    step_result.presentation.links[rev[:8]] = f'{REPO}/+/{rev}'

  def bisect_back(self, to_offset):
    """Bisects backwards from to_offset, doubling the delta in each iteration.

    Returns:
        A tuple of (from_offset, to_offset), where from_offset..to_offset
        represents the range of good..bad revision found.
    """
    commit_offset = 1
    for _ in range(MAX_BISECT_STEPS):
      from_offset = to_offset + commit_offset

      # Check if from_offset is a good revision, otherwise iterate backwards.
      from_offset = self.builds.find_closest_build(from_offset)
      self.report_revision('Checking %s', from_offset)
      if not self.is_bad_func(from_offset):
        return from_offset, to_offset

      to_offset = from_offset
      commit_offset *= 2

    raise self.api.step.StepFailure(
        'Could not find a good revision.')  # pragma: no cover

  def bisect_into(self, from_offset, to_offset):
    """Bisects into a given range from_offset..to_offset and determins a
    suspect commit range.
    """
    assert from_offset >= to_offset
    known_good = from_offset
    known_bad = to_offset
    self.report_range('Bisecting %s', from_offset, to_offset)
    for _ in range(MAX_BISECT_STEPS):
      # End of bisection. Note that possibly known_good..known_bad is a larger
      # range than 1 commit due to missing cas_digests.
      if from_offset - to_offset <= 1:
        return known_good, known_bad
      middle_offset = to_offset + (from_offset - to_offset) // 2
      build_offset = self.builds.find_closest_build(middle_offset, from_offset)

      if build_offset >= from_offset:
        self.report_range('No builds in %s', from_offset, middle_offset)
        # There are no cas_digests in lower half. Skip it and continue.
        from_offset = middle_offset
        continue

      self.report_revision('Checking %s', build_offset)
      if self.is_bad_func(build_offset):
        to_offset = build_offset
        known_bad = build_offset
      else:
        from_offset = build_offset
        known_good = build_offset


class RegressionBisector(Bisector):
  def bisect(self, known_bad_offset):
    from_offset, to_offset = self.bisect_back(known_bad_offset)
    from_offset, to_offset = self.bisect_into(from_offset, to_offset)
    result = self.report_range('Suspecting %s', from_offset, to_offset)
    return RawResult(status=common_pb.SUCCESS, summary_markdown=result)


class ProgressionBisector(Bisector):
  def __init__(self, api, depot, builds, is_bad_func):
    # For progression testing we invert the meaning of "is_bad".
    super().__init__(api, depot, builds, lambda *args: not is_bad_func(*args))

  def bisect(self, known_bad_offset):
    head_offset = self.builds.find_closest_build(self.depot.get_head_offset())

    # Check if this reproduces at ToT, if different from the already checked
    # known bad revision.
    if head_offset == known_bad_offset or not self.is_bad_func(head_offset):
      return RawResult(
          status=common_pb.FAILURE, summary_markdown='Flake still reproduces.')

    from_offset, to_offset = self.bisect_into(known_bad_offset, head_offset)
    result = self.report_range('Fixed in %s', from_offset, to_offset)
    return RawResult(status=common_pb.SUCCESS, summary_markdown=result)


class CombinedBisector(Validator):
  """Combines the regression and progression bisector steps."""

  def __init__(self, api, depot, builds, is_bad_func):
    super().__init__(api)
    self.progression = ProgressionBisector(api, depot, builds, is_bad_func)
    self.regression = RegressionBisector(api, depot, builds, is_bad_func)

  def bisect(self, known_bad_offset):
    # First try progression testing to quickly know if the problem is still
    # there.
    progression_result = self.progression.bisect(known_bad_offset)
    if progression_result.status == common_pb.SUCCESS:
      # A problem that's fixed doesn't need regression testing - we know
      # what fixed it.
      return progression_result

    # Problem still repros on ToT. Determine culprit and return a combined
    # report.
    regression_result = self.regression.bisect(known_bad_offset)
    return RawResult(
        status=regression_result.status,
        summary_markdown=f'{progression_result.summary_markdown}\n'
                         f'{regression_result.summary_markdown}')


class ReproBisector(Bisector):
  def validate(self, known_bad_reproduces):
    if not known_bad_reproduces:
      # We treat it as an error if a flake believed to repro, doesn't repro.
      raise self.api.step.StepFailure('Could not reproduce flake.')

  def bisect(self, known_bad_offset):
    return RawResult(
        status=common_pb.SUCCESS, summary_markdown='Flake still reproduces.')


BISECTORS = {
    'combined': CombinedBisector,
    'regression': RegressionBisector,
    'progression': ProgressionBisector,
    'repro': ReproBisector,
}


def setup_swarming(
    api, swarming_dimensions, swarming_priority, swarming_expiration):
  api.chromium_swarming.default_expiration = swarming_expiration
  api.chromium_swarming.default_hard_timeout = 60 * 60
  api.chromium_swarming.default_io_timeout = 20 * 60
  api.chromium_swarming.default_idempotent = False
  api.chromium_swarming.default_priority = swarming_priority
  api.chromium_swarming.default_user = 'v8-flake-bisect'
  api.chromium_swarming.add_default_tag('purpose:v8-flake-bisect')
  api.chromium_swarming.set_default_dimension('pool', 'chromium.tests')
  api.chromium_swarming.task_output_stdout = 'all'

  for item in swarming_dimensions:
    k, v = item.split(':')
    api.chromium_swarming.set_default_dimension(k, v)


def create_flakes_pyl_entry_step(api, config):
  """Generate config for flakes.pyl."""
  json_config = api.json.dumps(
      [config], indent=2, separators=(',', ': '), sort_keys=True)
  log = re.sub(
      r'([^,])(?=\n\s*[\}\]])', r'\1,', json_config,  # add trailing commas
      flags=re.MULTILINE).splitlines()                # split by line
  api.step('flakes.pyl entry', cmd=None).presentation.logs['config'] = log


def RunSteps(api: DEPS, bisect_builder_group, bisect_buildername, extra_args,
             failure_regexp, max_calibration_attempts, min_flake_threshold,
             isolated_name, mode, num_shards, outdir, override_flag_history,
             repetitions, revision, swarming_dimensions, swarming_priority,
             swarming_expiration, test_name, timeout_sec, total_timeout_sec,
             to_revision, variant):
  # Convert floats to ints.
  assert mode in BISECTORS
  repro_only = mode == 'repro'
  max_calibration_attempts = max(min(int(max_calibration_attempts), 5), 1)
  min_flake_threshold = max(min(int(min_flake_threshold), 4), 1)
  num_shards = int(num_shards)
  repetitions = int(repetitions)
  timeout_sec = int(timeout_sec)
  swarming_priority = max(min(int(swarming_priority), 255), 10)
  total_timeout_sec = int(total_timeout_sec)

  flag_history = list(FLAG_HISTORY)
  if override_flag_history:
    flag_history = [
        (int(num), old, new) for num, old, new in override_flag_history
    ]

  # Sort by commit position, newest first.
  flag_history.sort(reverse=True, key=(lambda entry: entry[0]))

  # Set up swarming client.
  setup_swarming(
      api, swarming_dimensions, swarming_priority, swarming_expiration)

  # Set up bisection helpers.
  depot = Depot(api, revision or to_revision)
  builds = Builds(
      api, depot, bisect_builder_group, bisect_buildername, isolated_name)
  command = Command(outdir, test_name, variant, repetitions, repro_only,
                    total_timeout_sec, timeout_sec, extra_args, flag_history,
                    min_flake_threshold)
  runner = Runner(api, builds, command, num_shards, repro_only,
                  max_calibration_attempts, min_flake_threshold, failure_regexp)
  bisector = BISECTORS[mode](api, depot, builds, runner.check_num_flakes)

  known_bad_offset = builds.find_closest_build(0)

  # Get confidence that the given revision is flaky and optionally calibrate the
  # repetitions.
  could_reproduce = runner.calibrate(known_bad_offset)
  bisector.validate(could_reproduce)

  create_flakes_pyl_entry_step(api, {
    'bisect_builder_group': bisect_builder_group,
    'bisect_buildername': bisect_buildername,
    'isolated_name': isolated_name,
    'test_name': test_name,
    'variant': variant,
    'extra_args': extra_args,
    'swarming_dimensions': swarming_dimensions,
    'timeout_sec': timeout_sec,
    'num_shards': runner.num_shards,
    # TODO(sergiyb): Drop total_timeout_sec here and just rely on
    # repetitions, which is more reliable on Windows. Right now,
    # however, we can't use it as it's not correctly calibrated when
    # total_timeout_sec is used. We should only implement this
    # suggestion once we can extract the actual number of repetitions
    # used from the test launcher after the calibration is done.
    'total_timeout_sec': total_timeout_sec * runner.multiplier,
    'repetitions': repetitions * runner.multiplier,
    'bug_url': '<bug-url>',
  })

  try:
    return bisector.bisect(known_bad_offset)
  except ReturnException as e:
    return RawResult(
        status=common_pb.FAILURE,
        summary_markdown=e.message,
    )


def GenTests(api: TEST_DEPS):

  def builder_properties(bisect_buildername='V8 Foobar',
                         mode='regression',
                         **properties):
    return api.properties(
        bisect_builder_group='foo.v8',
        bisect_buildername=bisect_buildername,
        extra_args=['--foo-flag', '--bar-flag'],
        isolated_name='foo_isolated',
        mode=mode,
        repetitions=64,
        swarming_dimensions=['os:Ubuntu-16.04', 'cpu:x86-64'],
        test_name='mjsunit/foobar',
        timeout_sec=20,
        revision='a0',
        variant='stress_foo',
        **properties)

  def successful_lookups(*offsets, fallback=False):
    suffix = ' (fallback)' if fallback else ''
    lookups = [
      api.override_step_data(
          f'gsutil lookup cas_digests for #{offset}{suffix}',
          api.raw_io.stream_output('', stream='stderr'),
          retcode=0,
      ) for offset in offsets
    ]
    return sum(lookups, api.empty_test_data())

  def _gitiles_log(*commit_message_pairs):
    return api.json.output({
      'log': [
        {'commit': commit, 'message': message}
        for commit, message in commit_message_pairs
      ],
    })

  def _gitiles_lookup(step_name, offset, count):
    return api.step_data(
        step_name,
        _gitiles_log(
           *((
              f'a{offset + i}',
              f'Cr-Commit-Position: refs/heads/main@{{#{99 - i - offset}}}',
            )
            for i in range(count))
        )
    )

  def init_head(offset, count, head_offset=0):
    return _gitiles_lookup(f'init head #{head_offset}', offset, count)

  def get_revisions(offset, count):
    return _gitiles_lookup(f'get revision #{offset}', offset, count)

  def is_flaky(offset,
               shard,
               flakes,
               calibration_attempt=0,
               test_name='mjsunit/foobar',
               output_prefix='',
               no_output=False):
    test_data = api.chromium_swarming.canned_summary_output_raw()
    if no_output:
      test_data['shards'] = [None]
    else:
      test_data['shards'][0]['output'] = (
          output_prefix + TEST_FAILED_TEMPLATE % flakes)
      test_data['shards'][0]['exit_code'] = 1
    step_prefix = ''
    if calibration_attempt:
      step_prefix = f'calibration attempt {calibration_attempt}.'
    step_name = f'check {test_name} at #{offset}'
    return api.step_data(
        f'{step_prefix}{step_name}.{step_name} - shard {shard} on Ubuntu-16.04',
        api.chromium_swarming.summary(
            dispatched_task_step_test_data=None, raw_summary=test_data))

  def _verify_result(message, from_offset, to_offset):
    """Verify that the correct reporting step for from_offset..to_offset is
    emitted.
    """
    git_range = f'a{from_offset}..a{to_offset}'
    step_name = f'{message} #{from_offset}..#{to_offset}'
    def suspects_internal(check, steps):
      check(steps[step_name].links[git_range] == f'{REPO}/+log/{git_range}')
    return api.post_process(suspects_internal)

  def verify_suspects(from_offset, to_offset):
    return _verify_result('Suspecting', from_offset, to_offset)

  def verify_fixed(from_offset, to_offset):
    return _verify_result('Fixed in', from_offset, to_offset)

  def drop_test_step_expectations():
    return api.post_process(
        Filter().include_re(r'(?!^check mjsunit/foobar)'))

  def one_bisect_iteration(index):
    exp_index = 2**index - 1
    return (get_revisions(exp_index, 1) + successful_lookups(exp_index) +
            is_flaky(exp_index, 0, 1))

  # Full bisect run with some corner cases. Overview of all revisions ordered
  # new -> old.
  # a0: no cas digest
  # a1: not flaky enough with 64 but flaky with 128 repetitions
  # a2: flaky
  # a3: flaky
  # a4: no cas digest
  # a5: not flaky
  # -> Should result in suspecting range a5..a3.
  yield api.test(
      'full_bisect',
      # Test path where total timeout isn't used.
      builder_properties('V8 Foobar - builder', total_timeout_sec=0),
      # Data for resolving offsets to git hashes. Simulate gitiles page size of
      # 3 commits per call.
      get_revisions(1, 3),
      get_revisions(4, 3),
      # CAS digest data simulation for all existing revisions.
      successful_lookups(1, 2, 3, 5),
      # Calibration. We check for flakes until enough are found. First only one
      # shard reports 2 failures.
      is_flaky(1, 1, 2, calibration_attempt=1),
      # Then 3 shards report 5 failures total.
      is_flaky(1, 0, 2, calibration_attempt=2),
      is_flaky(1, 1, 1, calibration_attempt=2),
      is_flaky(1, 2, 2, calibration_attempt=2),
      # Bisect backwards from a1 until good revision a5 is found.
      is_flaky(2, 0, 3),
      # Bisect into a5..a2.
      is_flaky(3, 0, 3),
      verify_suspects(5, 3),
      drop_test_step_expectations(),
  )

  # Similar to above but fewer corner cases. This is for simulating bisection
  # going into the upper half of a git range, which has different code paths
  # above.
  yield api.test(
      'full_bisect_upper',
      builder_properties(),
      # Data for resolving offsets to git hashes. Simulate gitiles page size of
      # 8, fetching all data in the first call.
      get_revisions(1, 8),
      # CAS digest data simulation for all revisions.
      successful_lookups(0, 1, 3, 4, 5, 7),
      # Calibration.
      is_flaky(0, 0, 5, calibration_attempt=1),
      # Bisect backwards from a0 until good revision a7 is found.
      is_flaky(1, 0, 3),
      is_flaky(3, 0, 3),
      # Bisect into a7..a3.
      is_flaky(4, 0, 2),
      verify_suspects(5, 4),
      api.post_process(DropExpectation),
  )

  # Test bisecting through a large range of missing builds.
  yield api.test(
      'large_gap',
      builder_properties(),
      get_revisions(1, 4),
      # Simulate a large gap between #0 and #4..
      successful_lookups(0, 4),
      # Bad build #0 wile #4 is a good build using default test data.
      is_flaky(0, 0, 5, calibration_attempt=1),
      # Check that bisect continues properly after not finding a build in one
      # half.
      api.post_process(MustRun, 'No builds in #4..#2'),
      api.post_process(MustRun, 'No builds in #2..#1'),
      # Check that CAS lookup is cached for the negative case. We look only
      # once for a build that's not found.
      api.post_process(MustRun, 'gsutil lookup cas_digests for #2'),
      api.post_process(DoesNotRun, 'gsutil lookup cas_digests for #2 (2)'),
      verify_suspects(4, 0),
      api.post_process(DropExpectation),
  )

  # Progression testing with the revisions from ToT not overlapping with
  # the known bad revision. The flake is fixed at ToT.
  yield api.test(
      'progression',
      builder_properties(mode='progression'),
      # Progression testing fetches ToT at a-9. No initial overlap with a0.
      # Iterate until a0 is reached.
      init_head(-9, 4, head_offset=0),
      init_head(-5, 4, head_offset=4),
      init_head(-1, 4, head_offset=8),
      # Simulate existing builds.
      successful_lookups(-9, -8, -7, -5, 0),
      # Calibration with successful repro at offset 0.
      is_flaky(0, 0, 5, calibration_attempt=1),
      # The flake still reproduces until -7.
      is_flaky(-5, 0, 2),
      is_flaky(-7, 0, 2),
      verify_fixed(-7, -8),
      drop_test_step_expectations(),
  )

  # Progression testing with the revisions from ToT overlapping with
  # the known bad revision.
  yield api.test(
      'progression_overlap',
      builder_properties(mode='progression'),
      # Revision at offset 1 is looked up because there is no CAS digest at 0
      # in this test. The call will fetch some more revisions that we won't
      # need.
      get_revisions(1, 3),
      # Progression testing fetches ToT at a-6. Here we simulate the fetched
      # range to overlap with what we already fetched above.
      init_head(-6, 8),
      # CAS digest data simulation for all revisions. We simulate missing a
      # couple of builds, e.g. at 0.
      successful_lookups(-5, -4, -2, 1),
      # Calibration with successful repro at offset 1.
      is_flaky(1, 0, 5, calibration_attempt=1),
      # The flake still reproduces at -2. For -3 there's no build, resulting
      # in a fixed range of -2..-4.
      is_flaky(-2, 0, 3),
      verify_fixed(-2, -4),
      api.post_process(DropExpectation),
  )

  # Progression testing where flake still reproduces.
  yield api.test(
      'progression_still_reproduces',
      builder_properties(mode='progression'),
      # Initial fetch covers all required revisions.
      init_head(-3, 4),
      # Simulate existing builds.
      successful_lookups(-3, 0),
      # Calibration with successful repro at offset 0.
      is_flaky(0, 0, 5, calibration_attempt=1),
      # The flake still reproduces.
      is_flaky(-3, 0, 2),
      api.post_process(SummaryMarkdown, 'Flake still reproduces.'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  # Progression testing starting with ToT revision.
  yield api.test(
      'progression_on_tot_revision',
      builder_properties(mode='progression'),
      # Initial fetch covers all required revisions.
      init_head(0, 1),
      # Simulate existing builds.
      successful_lookups(0),
      # Calibration with successful repro at offset 0.
      is_flaky(0, 0, 5, calibration_attempt=1),
      api.post_process(SummaryMarkdown, 'Flake still reproduces.'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  # Progression testing with a too large gap between known bad revision
  # and ToT.
  yield api.test(
      'progression_large_gap',
      builder_properties(mode='progression'),
      # Progression testing fetches ToT at a commit with an offset to
      # a0 larger than MAX_HEAD_OFFSET.
      init_head(-MAX_HEAD_OFFSET - 1, MAX_HEAD_OFFSET, head_offset=0),
      successful_lookups(0),
      is_flaky(0, 0, 5, calibration_attempt=1),
      api.post_process(
          SummaryMarkdown,
          f'Could not connect the known bad revision to refs/heads/main. '
          f'Looked in over {MAX_HEAD_OFFSET} commits.'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  # Combine progression and regression testing.
  yield api.test(
      'combined',
      builder_properties(mode='combined'),
      # Initial fetch covers all required revisions.
      init_head(-3, 4),
      # Simulate existing builds.
      successful_lookups(-3, 0, 1, 2, 3),
      # Calibration with successful repro at offset 0.
      is_flaky(0, 0, 5, calibration_attempt=1),
      # The flake still reproduces.
      is_flaky(-3, 0, 2),
      # Data for regression testing.
      get_revisions(1, 3),
      is_flaky(1, 0, 3),
      is_flaky(2, 0, 3),
      verify_suspects(3, 2),
      api.post_process(SummaryMarkdownRE, r'Flake still reproduces'),
      api.post_process(SummaryMarkdownRE,
                       re.escape(f'Suspecting [#3..#2]({REPO}/+log/a3..a2)')),
      api.post_process(DropExpectation),
  )

  # Combine progression and regression testing but flake is already fixed.
  yield api.test(
      'combined_fixed',
      builder_properties(mode='combined'),
      # Progression testing fetches ToT at a-3.
      init_head(-3, 4),
      # Simulate existing builds.
      successful_lookups(-3, -2, -1, 0),
      # Calibration with successful repro at offset 0.
      is_flaky(0, 0, 5, calibration_attempt=1),
      is_flaky(-1, 0, 2),
      verify_fixed(-1, -2),
      api.post_process(SummaryMarkdown,
                       f'Fixed in [#-1..#-2]({REPO}/+log/a-1..a-2)'),
      api.post_process(DropExpectation),
  )

  # TODO(machenbach): Share this with the devtools-frontend recipe.
  def check_task_arg(check, steps, step, arg, inverse):
    """Check that `arg` is passed to the command of the swarming task of a
    step. Or that it's not passed if `inverse` is True.
    """
    check(step in steps)

    # A json string, configuring the task, is passed to the swarming command.
    check('-json-input' in steps[step].cmd)
    json_arg_index = steps[step].cmd.index('-json-input')
    check(len(steps[step].cmd) > json_arg_index)

    # The config must be valid json.
    json_input = json.loads(steps[step].cmd[json_arg_index + 1])

    # Dig down the structure to the wrapped command passed to the swarming
    # task.
    check(json_input)
    requests = json_input.get('requests', [])
    check(requests)
    task_slices = requests[0].get('task_slices', [])
    check(task_slices)
    check('properties' in task_slices[0])
    check('command' in task_slices[0]['properties'])
    command = task_slices[0]['properties']['command']
    check(command)

    # Check if the argument is part of this command.
    if inverse:
      check(arg not in command)
    else:
      check(arg in command)

  def check_arg_in_task(check, steps, step, arg):
    check_task_arg(check, steps, step, arg, inverse=False)

  def check_arg_not_in_task(check, steps, step, arg):
    check_task_arg(check, steps, step, arg, inverse=True)

  tr_tmpl = ('check mjsunit/foobar at #%d.[trigger] check mjsunit/foobar '
             'at #%d - shard 1 on Ubuntu-16.04')
  yield api.test(
      'bisect_flag_rewrite',
      # Test that flags are rewritten according to this history.
      # --foo-flag -> --baz before position 96 (or offset #3)
      # --baz -> --baam before position 94 (or offset #5)
      # --bar-flag removed before position 95 (or offset #4)
      builder_properties(override_flag_history=[[94, '--baam', '--baz'],
                                                [96, '--baz', '--foo-.*'],
                                                [95, None, '--bar-.*']]),
      # Data for resolving offsets to git hashes. Simulate gitiles page size of
      # 8, fetching all data in the first call.
      get_revisions(1, 8),
      # CAS digest data simulation for all revisions.
      successful_lookups(0, 1, 3, 4, 5, 7),
      # Calibration.
      is_flaky(0, 0, 5, calibration_attempt=1),
      # Bisect backwards from a0 until good revision a7 is found.
      is_flaky(1, 0, 3),
      is_flaky(3, 0, 3),
      # Bisect into a7..a3.
      is_flaky(4, 0, 2),
      verify_suspects(5, 4),

      # Check rewrites work for --foo-flag and --baz.
      api.post_process(check_arg_in_task, tr_tmpl % (1, 1), '--foo-flag'),
      api.post_process(check_arg_in_task, tr_tmpl % (3, 3), '--foo-flag'),
      api.post_process(check_arg_in_task, tr_tmpl % (4, 4), '--baz'),
      api.post_process(check_arg_in_task, tr_tmpl % (5, 5), '--baz'),
      api.post_process(check_arg_in_task, tr_tmpl % (7, 7), '--baam'),

      # Check the reverse.
      api.post_process(check_arg_not_in_task, tr_tmpl % (1, 1), '--baz'),
      api.post_process(check_arg_not_in_task, tr_tmpl % (1, 1), '--baam'),
      api.post_process(check_arg_not_in_task, tr_tmpl % (3, 3), '--baz'),
      api.post_process(check_arg_not_in_task, tr_tmpl % (3, 3), '--baam'),
      api.post_process(check_arg_not_in_task, tr_tmpl % (4, 4), '--foo-flag'),
      api.post_process(check_arg_not_in_task, tr_tmpl % (4, 4), '--baam'),
      api.post_process(check_arg_not_in_task, tr_tmpl % (5, 5), '--foo-flag'),
      api.post_process(check_arg_not_in_task, tr_tmpl % (5, 5), '--baam'),
      api.post_process(check_arg_not_in_task, tr_tmpl % (7, 7), '--foo-flag'),
      api.post_process(check_arg_not_in_task, tr_tmpl % (7, 7), '--baz'),

      # Check removal works for the --bar-flag
      api.post_process(check_arg_in_task, tr_tmpl % (1, 1), '--bar-flag'),
      api.post_process(check_arg_in_task, tr_tmpl % (3, 3), '--bar-flag'),
      api.post_process(check_arg_in_task, tr_tmpl % (4, 4), '--bar-flag'),
      api.post_process(check_arg_not_in_task, tr_tmpl % (5, 5), '--bar-flag'),
      api.post_process(check_arg_not_in_task, tr_tmpl % (7, 7), '--bar-flag'),
      api.post_process(DropExpectation),
  )

  # Simulate not finding any cas_digests.
  yield api.test(
      'no_cas_digests',
      builder_properties(),
      sum((get_revisions(i, 1) for i in range(1, MAX_CAS_OFFSET)),
          api.empty_test_data()),
      api.post_process(SummaryMarkdown, 'Couldn\'t find cas_digests.'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  # Simulate not returning a JSON output after a few iterations.
  yield api.test(
      'short_bisection_with_no_json_output',
      builder_properties(),
      successful_lookups(0),
      is_flaky(0, 0, 5, calibration_attempt=1),
      sum((one_bisect_iteration(i) for i in range(1, 5)),
          api.empty_test_data()),
      is_flaky(15, 0, 1, no_output=True),
      api.post_process(SummaryMarkdownRE,
                       'Infra Failure.*missing shard results.*'),
      api.post_process(DropExpectation),
      status='INFRA_FAILURE',
  )

  # Simulate repro-only mode reproducing a flake.
  yield api.test(
      'repro_only', builder_properties(mode='repro'), successful_lookups(0),
      is_flaky(0, 0, 1, calibration_attempt=1),
      api.post_process(SummaryMarkdown, 'Flake still reproduces.'),
      api.post_process(
          Filter(
              'calibration attempt 1.check mjsunit/foobar at #0.'
              '[trigger] check mjsunit/foobar at #0 - shard 0 on Ubuntu-16.04'))
  )

  # Simulate repro-only mode not reproducing a flake.
  yield api.test(
      'repro_only_failed',
      builder_properties(mode='repro'),
      successful_lookups(0),
      api.post_process(SummaryMarkdown, 'Could not reproduce flake.'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  # Simulate repro-only mode with no revision property given.
  yield api.test(
      'repro_only_tot',
      builder_properties(mode='repro'),
      api.properties(revision=None),
      init_head(0, 4, head_offset=0),
      successful_lookups(0),
      is_flaky(0, 0, 1, calibration_attempt=1),
      api.post_process(MustRun, 'init head #0'),
      api.post_process(SummaryMarkdown, 'Flake still reproduces.'),
      api.post_process(DropExpectation),
  )

  # Simulate repro-only mode using a fallback debug builder.
  yield api.test(
      'repro_only_fallback',
      builder_properties('V8 Foobar - debug builder', mode='repro'),
      get_revisions(1, 2),
      successful_lookups(1, fallback=True),
      api.post_process(MustRun, 'gsutil lookup cas_digests for #0'),
      api.post_process(MustRun, 'gsutil lookup cas_digests for #0 (fallback)'),
      api.post_process(MustRun, 'gsutil lookup cas_digests for #1'),
      api.post_process(MustRun, 'gsutil lookup cas_digests for #1 (fallback)'),
      api.post_process(DoesNotRun, 'gsutil lookup cas_digests for #2'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  # Simulate repro-only mode reproducing a flake by regexp.
  yield api.test(
      'repro_regexp_match',
      builder_properties(mode='repro', failure_regexp='foo.*bar'),
      successful_lookups(0),
      is_flaky(
          0,
          0,
          1,
          calibration_attempt=1,
          output_prefix='has foo and bar in the output...\n'),
      api.post_process(SummaryMarkdown, 'Flake still reproduces.'),
      api.post_process(DropExpectation),
  )

  # Simulate digest not found in CAS.
  yield api.test(
      'repro_digest_not_found',
      builder_properties(mode='regression'),
      get_revisions(1, 1),
      successful_lookups(0, 1),
      is_flaky(0, 0, 5, calibration_attempt=1),
      is_flaky(1, 0, 0, output_prefix='abc123/123 not found in the CAS\n'),
      api.post_process(
          SummaryMarkdown,
          'Bisect goes too far back. Builds went out of retention.'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  # Simulate repro-only mode not reproducing a flake by regexp.
  yield api.test(
      'repro_regexp_no_match',
      builder_properties(mode='repro', failure_regexp='foo.*bar'),
      successful_lookups(0),
      is_flaky(0, 0, 1, calibration_attempt=1),
      api.post_process(SummaryMarkdown, 'Could not reproduce flake.'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  # Simulate running tasks on Android and verify correct dimensions.
  def check_dimensions(check, steps):
    step = ('calibration attempt 1.check mjsunit/foobar at #0.'
            '[trigger] check mjsunit/foobar at #0 - shard 0 on Android')
    if check(step in steps):
      check(all(arg != 'cpu' for arg in steps[step].cmd))

  swarming_dimensions = [
    'os:Android',
    'cpu:x86-64',
    'device_os:MMB29Q',
    'device_type:bullhead',
    'pool:chromium.tests',
  ]
  yield api.test(
      'android_dimensions',
      builder_properties(mode='repro'),
      api.properties(swarming_dimensions=swarming_dimensions),
      successful_lookups(0),
      api.post_process(check_dimensions),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  # Simulate not finding enough flakes during calibration.
  # Also test cutting off overly long test names in step names.
  long_test_name = (29 * '*') + 'too_long'
  shortened_test_name = (29 * '*') + '...'
  yield api.test(
      'no_confidence',
      builder_properties(num_shards=8),
      api.properties(test_name=long_test_name),
      successful_lookups(0),
      is_flaky(0, 0, 0, calibration_attempt=1, test_name=shortened_test_name),
      is_flaky(0, 1, 2, calibration_attempt=2, test_name=shortened_test_name),
      is_flaky(0, 2, 1, calibration_attempt=3, test_name=shortened_test_name),
      is_flaky(0, 1, 3, calibration_attempt=4, test_name=shortened_test_name),
      is_flaky(0, 0, 3, calibration_attempt=5, test_name=shortened_test_name),
      api.post_process(SummaryMarkdown, 'Could not reach enough confidence.'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  # Simulate triggering of the recipe by the flake verification bot.
  yield api.test(
      'verify_flake',
      builder_properties(
          mode='repro',
          swarming_priority=40,
          num_shards=2,
          swarming_expiration=7200,
          total_timeout_sec=240,
          max_calibration_attempts=1),
      successful_lookups(0),
      is_flaky(0, 0, 0, calibration_attempt=1),
      is_flaky(0, 1, 1, calibration_attempt=1),
      api.post_process(SummaryMarkdown, 'Flake still reproduces.'),
      api.post_process(
          Filter(
              'calibration attempt 1.check mjsunit/foobar at #0.'
              '[trigger] check mjsunit/foobar at #0 - shard 1 on Ubuntu-16.04',
              'calibration attempt 1.check mjsunit/foobar at #0.'
              'check mjsunit/foobar at #0 - shard 1 on Ubuntu-16.04')),
      status='SUCCESS',
  )

  yield api.test(
      'bisect_attempt_with_wrong_commit_position',
      builder_properties(),
      api.step_data(
          'get revision #1',
          _gitiles_log(
              ('a1', 'Cr-Commit-Position-Incorrect: refs/heads/main@{#42}')),
      ),
      api.expect_exception('ValueError'),
      api.post_process(DropExpectation),
      status='INFRA_FAILURE',
  )

  yield api.test(
      'bisect_attempt_with_revert_commit_position',
      builder_properties(),
      api.step_data(
          'get revision #1',
          _gitiles_log(('a1', '> Cr-Commit-Position: refs/heads/main@{#42}\n'
                        'Cr-Commit-Position: refs/heads/main@{#100}')),
      ),
      successful_lookups(0, 1),
      is_flaky(0, 0, 5, calibration_attempt=1),
      api.post_process(MustRun, 'Checking #1 (commit position: 100)'),
      api.post_process(DoesNotRun, 'Checking #1 (commit position: 42)'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )
