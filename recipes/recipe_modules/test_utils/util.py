# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr
import collections
import sys

from . import canonical
from google.protobuf import json_format

from PB.go.chromium.org.luci.resultdb.proto.v1 import common as common_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  test_result as test_result_pb2,
)

from RECIPE_MODULES.build.attr_utils import (
  attrib,
  attrs,
  mapping,
  sequence,
  set_,
)


class GTestResults:
  MAX_LOG_LINES = 5000

  def __init__(self, jsonish=None):
    self.logs = {}
    self.raw = jsonish or {}
    self.pass_fail_counts = {}

    # In the short term, the FindIt logic for ignoring NOTRUN/UNKNOWN tests
    # [treating them as equivalent to a passing test] is implemented in the
    # chromium tests recipe.
    # https://bugs.chromium.org/p/chromium/issues/detail?id=872042#c32

    # A set of tests which were never run [every test result was NOTRUN or
    # UNKNOWN or SKIPPED].
    self.findit_notrun = set()

    # Stores raw results of each test. Used to display test results in build
    # step logs.
    self.raw_results = collections.defaultdict(list)

    if not jsonish:
      self.valid = False
      return

    self.valid = True

    for cur_iteration_data in self.raw.get('per_iteration_data', []):
      for test_fullname, results in cur_iteration_data.items():
        # TODO (robertocn): Consider a failure in any iteration a failure of
        # the whole test, but allow for an override that makes a test pass if
        # it passes at least once.
        self.pass_fail_counts.setdefault(
          test_fullname, {'pass_count': 0, 'fail_count': 0}
        )
        self.logs.setdefault(test_fullname, [])
        for cur_result in results:
          if cur_result['status'] == 'SUCCESS':
            self.pass_fail_counts[test_fullname]['pass_count'] += 1
          elif cur_result['status'] != 'SKIPPED':
            self.pass_fail_counts[test_fullname]['fail_count'] += 1
          else:
            self.pass_fail_counts[test_fullname]['fail_count'] = 0
          self.raw_results[test_fullname].append(cur_result['status'])

          ascii_log = cur_result['output_snippet'].encode(
            'ascii', errors='replace'
          )
          self.logs[test_fullname].extend(
            self._compress_list(ascii_log.splitlines())
          )

    for test_fullname, results in self.raw_results.items():
      # These strings are defined by base/test/launcher/test_result.cc.
      # https://cs.chromium.org/chromium/src/base/test/launcher/test_result.cc
      unique_results = set(results)
      if unique_results.issubset(set(['UNKNOWN', 'NOTRUN', 'SKIPPED'])):
        self.findit_notrun.add(test_fullname)

  @property
  def total_tests_ran(self):
    return len(self.pass_fail_counts)

  @property
  def unique_failures(self):
    """Returns the set of tests that failed at least once."""
    failures = set()
    for test_name, results_dict in self.pass_fail_counts.items():
      if results_dict['fail_count'] >= 1:
        failures.add(test_name)
    return failures

  @property
  def deterministic_failures(self):
    return canonical.deterministic_failures(self.canonical_result_format())

  def _compress_list(self, lines):
    if len(lines) > self.MAX_LOG_LINES:
      remove_from_start = self.MAX_LOG_LINES // 2
      return (
        lines[:remove_from_start]
        + ['<truncated>']
        + lines[len(lines) - (self.MAX_LOG_LINES - remove_from_start) :]
      )
    return lines

  def canonical_result_format(self):
    """Returns a dictionary with results in canonical format."""
    global_tags = self.raw.get('global_tags', [])
    unreliable = 'UNRELIABLE_RESULTS' in global_tags
    interrupted = 'CAUGHT_TERMINATION_SIGNAL' in global_tags
    return canonical.result_format(
      valid=self.valid and not unreliable and not interrupted,
      failures=sorted(self.unique_failures),
      total_tests_ran=self.total_tests_ran,
      pass_fail_counts=self.pass_fail_counts,
      findit_notrun=self.findit_notrun,
    )


@attrs()
class RDBPerIndividualTestResults:
  """Contains result info of an individual test as returned by RDB.

  "individual test" is uniquely identified by test id. For each individual test
  within a test_suite, there could be multiple test results from being retried,
  or repeated within shards of the suite. These result info are stored in
  |statuses|, |expectednesses|, etc.
  """

  # NOTE: If you add an attribute here, make sure to reflect the change in
  # update get_size_in_mem() below.
  # Read from any result's test_name tag. If not exist, use the part of test_id
  # after test_id_prefix.
  # e.g. Service/FeatureInfoTest.Basic/0
  test_name = attrib(str)
  # Full test ID.
  # e.g. ninja://gpu:gpu_unittests/FeatureInfoTest.Basic/Service.0
  test_id = attrib(str)
  # Full invocation ID that includes the swarming task ID
  # e.g. task-chromium-swarm.appspot.com-5e052f4430ead411
  invocation_id = attrib(str)
  # A duration of any passed run.
  duration_milliseconds = attrib(int, default=None)
  # |statuses| and |expectednesses| are outcomes of single results.
  # Values at each index are for the same test run.
  statuses = attrib(sequence[...])
  expectednesses = attrib(sequence[bool])
  # Reasons of all results corresponding to |statuses|. Empty str if the
  # raw RDB result doesn't have this stored.
  failure_reasons = attrib(sequence[str])
  # The path to the file associated with this test result. Defined in proto
  # under TestMetadata.Location.FileName. See proto at
  # https://source.chromium.org/chromium/infra/infra/+/main:
  # go/src/go.chromium.org/luci/resultdb/proto/v1/test_result.proto
  # e.g. //chrome/browser/top_level_storage_access_api/some_test.cc
  test_metadata_file_name = attrib(str)

  @classmethod
  def create(cls, test_id, test_results, test_id_prefix, invocation_id):
    """
    Args:
      test_id: The test ID of results.
      test_results: All results of the test id.
      test_id_prefix: The test ID prefix of the |RDBPerSuiteResults| where this
        result is grouped into.
      invocation_id: Invocation ID of the test.
    """
    duration_milliseconds = None
    test_name = None
    test_id = ''
    statuses = [tr.status for tr in test_results]
    expectednesses = [tr.expected for tr in test_results]
    failure_reasons = [
      tr.failure_reason.primary_error_message or '' for tr in test_results
    ]
    test_metadata_file_name = ''

    for tr in test_results:
      test_id = tr.test_id
      # Durations of expected runs or unexpected passed (exonerated) runs are
      # considered valid. Use duration of the last passed or expected result
      # with duration.
      if (tr.expected or tr.status == test_result_pb2.PASS) and tr.duration:
        duration_milliseconds = int(
          tr.duration.seconds * 1000 + int(tr.duration.nanos / 1000000.0)
        )
      # Use test name tag of the last result with the tag.
      for tag in tr.tags:
        if tag.key == 'test_name':
          test_name = tag.value

      test_metadata = getattr(tr, 'test_metadata', None)
      if test_metadata:
        location_meta = getattr(test_metadata, 'location', None)
        if location_meta:
          test_metadata_file_name = location_meta.file_name

    assert test_id.startswith(test_id_prefix)
    # If not found in tags, use the part after test id prefix in test ID.
    if not test_name:
      test_name = test_id[len(test_id_prefix) :]

    return cls(
      test_name=test_name,
      test_id=test_id,
      invocation_id=invocation_id,
      duration_milliseconds=duration_milliseconds,
      statuses=statuses,
      expectednesses=expectednesses,
      failure_reasons=failure_reasons,
      test_metadata_file_name=test_metadata_file_name,
    )

  def total_test_count(self):
    return len(self.statuses)

  def unexpected_unpassed_count(self):
    return sum(
      (status != test_result_pb2.PASS and not expected)
      for status, expected in zip(self.statuses, self.expectednesses)
    )

  def get_size_in_mem(self):
    total = sys.getsizeof(self)
    total += sys.getsizeof(self.test_id)
    total += sys.getsizeof(self.test_name)
    total += sys.getsizeof(self.duration_milliseconds)
    total += sys.getsizeof(self.test_metadata_file_name)
    # self.statuses and self.expectednesses are lists of very few unique
    # elements. So don't bother counting the size of each element since each
    # unique element is only stored once.
    total += sys.getsizeof(self.statuses)
    total += sys.getsizeof(self.expectednesses)
    # Assume the elements in self.failure_reasons are all unique. So count
    # both the size of the list, and the size of each element.
    total += sys.getsizeof(self.failure_reasons)
    for reason in self.failure_reasons:
      total += sys.getsizeof(reason)
    return total


@attrs()
class RDBPerSuiteResults:
  """Contains results of a single test suite as returned by RDB."""

  NEEDED_FIELDS = [
    'testId',
    'variant',
    'variantHash',
    'status',
    'tags',
    'expected',
    'duration',
    'failureReason',
    'testMetadata.location',
  ]

  # NOTE: If you add an attribute here, make sure to reflect the change in
  # update get_size_in_mem() below.
  suite_name = attrib(str)
  variant = attrib(common_pb2.Variant)
  variant_hash = attrib(str)
  # NOTE: total_tests_ran is the total number of test results in the suite,
  # *not* test cases. So if there were 10 test cases in a suite, and they
  # all retried once, total_tests_ran would be 20.
  # TODO(crbug.com/388310028): Clarify the name of this var once RDB returns
  # both stats.
  total_tests_ran = attrib(int)
  unexpected_passing_tests = attrib(set_[RDBPerIndividualTestResults])
  unexpected_failing_tests = attrib(set_[RDBPerIndividualTestResults])
  # unexpected_skipped_tests should be a subset of unexpected_failing_tests.
  unexpected_skipped_tests = attrib(set_[RDBPerIndividualTestResults])
  invalid = attrib(bool, default=False)
  # A mapping from test name str to its |RDBPerIndividualTestResults| object
  # for tests without any expected results.
  individual_unexpected_test_by_test_name = attrib(
    mapping[str, RDBPerIndividualTestResults]
  )
  # A list of all |RDBPerIndividualTestResults| objects within this class.
  all_tests = attrib(sequence[RDBPerIndividualTestResults])
  # |test_id_prefix| from the test specs in testing/buildbot. Empty str if it's
  # not set, or if any test IDs from invocations don't have the exact prefix
  # as input.
  test_id_prefix = attrib(str, default='')
  # This is a field used in |with_failure_on_exit| method.
  exists_unexpected_failing_result = attrib(bool, default=False)

  @classmethod
  def create(
    cls,
    invocations,
    suite_name,
    test_id_prefix,
    total_tests_ran,
    allow_flaky_passes=True,
    failure_on_exit=False,
  ):
    """
    Args:
      invocations, dict of {invocation_id: api.resultdb.Invocation} as
          returned by resultdb recipe_module's query().
      allow_flaky_passes: If True and an individual test ran multiple times
          and passes at least once, the individual test is passing. If False
          and the individual test passed and failed, the test is failing.
          For example, if an individual test ran multiple times with
          [FAIL, PASS, PASS, FAIL, PASS], the test will be categorized as
          unexpectedly failing. This param is useful for retry wo patch suite
          runs because the recipe is checking if failing tests fail at least
          once without patch.
      failure_on_exit: If True, indicates the test harness/runner exited with a
          non-zero exit code. If this occurs and no unexpected failures were
          reported, it indicates invalid test results.
    """
    exists_unexpected_failing_result = False
    results_by_test_id = collections.defaultdict(list)
    test_id_to_invocation_id = {}
    variant = common_pb2.Variant()
    variant_hash = ''
    total_results = 0
    test_id_prefix = test_id_prefix or ''

    for inv_id, inv in invocations.items():
      total_results += len(inv.test_results)
      for tr in inv.test_results:
        variant_def = getattr(tr.variant, 'def')
        inv_name = variant_def['test_suite']
        # A RDBPerSuiteResults instance shouldn't be created with invocations
        # from different suites.
        if inv_name and suite_name:
          assert inv_name == suite_name, "Mismatched invocations, %s vs %s" % (
            inv_name,
            suite_name,
          )
        variant = tr.variant
        variant_hash = tr.variant_hash
        results_by_test_id[tr.test_id].append(tr)
        test_id_to_invocation_id[tr.test_id] = inv_id
        # Use empty test_id_prefix if there is conflict.
        if not tr.test_id.startswith(test_id_prefix):
          test_id_prefix = ''

    total_tests_ran = total_tests_ran or total_results
    unexpected_failing_tests = set()
    unexpected_passing_tests = set()
    unexpected_skipped_tests = set()
    individual_unexpected_test_by_test_name = {}
    all_tests = []
    for test_id, test_results in results_by_test_id.items():
      individual_test = RDBPerIndividualTestResults.create(
        test_id=test_id,
        test_results=test_results,
        test_id_prefix=test_id_prefix,
        invocation_id=test_id_to_invocation_id[test_id],
      )
      if individual_test.unexpected_unpassed_count() > 0:
        exists_unexpected_failing_result = True
      all_tests.append(individual_test)
      # This filters out any tests that were auto-retried within the
      # invocation and finished with an expected result. eg: a test that's
      # expected to CRASH and runs with results [FAIL, CRASH]. RDB returns
      # these results, but we don't consider them interesting for the
      # purposes of recipe retry/pass/fail decisions.
      if allow_flaky_passes:
        if any(tr.expected for tr in test_results):
          continue
      else:
        if any(not tr.expected for tr in test_results):
          unexpected_failing_tests.add(individual_test)
          if all(tr.status == test_result_pb2.SKIP for tr in test_results):
            unexpected_skipped_tests.add(individual_test)
          continue
      if all(tr.expected for tr in test_results):
        continue
      individual_unexpected_test_by_test_name[individual_test.test_name] = (
        individual_test
      )
      if all(tr.status != test_result_pb2.PASS for tr in test_results):
        unexpected_failing_tests.add(individual_test)
        if all(tr.status == test_result_pb2.SKIP for tr in test_results):
          unexpected_skipped_tests.add(individual_test)
      else:
        unexpected_passing_tests.add(individual_test)

    # If there were no unexpected failing results, but the harness exited
    # non-zero, assume something went wrong in the test setup/init (eg: failure
    # in underlying hardware) and that the results are invalid.
    invalid = failure_on_exit and not unexpected_failing_tests

    return cls(
      suite_name=suite_name,
      variant=variant,
      variant_hash=variant_hash,
      total_tests_ran=total_tests_ran,
      unexpected_passing_tests=unexpected_passing_tests,
      unexpected_failing_tests=unexpected_failing_tests,
      unexpected_skipped_tests=unexpected_skipped_tests,
      invalid=invalid,
      individual_unexpected_test_by_test_name=(
        individual_unexpected_test_by_test_name
      ),
      all_tests=all_tests,
      test_id_prefix=test_id_prefix,
      exists_unexpected_failing_result=exists_unexpected_failing_result,
    )

  def with_failure_on_exit(self, failure_on_exit):
    """Returns a new instance with an updated |invalid| value.

    We may end up fetching a test's RDB results before we know its exit code.
    (e.g. We query RDB before we collect swarming tasks.)

    TODO(crbug.com/1245085): The ordering of events doesn't affect the results
    in the new Milo UI. So this functionality can be torn out when all users
    have migrated.

    Args:
      failure_on_exit: If True, indicates the test harness/runner exited with a
          non-zero exit code. If this occurs and no unexpected results were
          reported, it indicates invalid test results.
    """
    return attr.evolve(
      self, invalid=(failure_on_exit and not self.unexpected_failing_tests)
    )

  def to_jsonish(self):

    def _names_of_tests(tests):
      return sorted([t.test_name for t in tests])

    jsonish_repr = {
      'suite_name': self.suite_name,
      'test_id_prefix': self.test_id_prefix,
      'variant_hash': self.variant_hash,
      'invalid': str(self.invalid),
      'total_tests_ran': self.total_tests_ran,
      'unexpected_passing_tests': _names_of_tests(
        self.unexpected_passing_tests
      ),
      'unexpected_failing_tests': _names_of_tests(
        self.unexpected_failing_tests
      ),
      'unexpected_skipped_tests': _names_of_tests(
        self.unexpected_skipped_tests
      ),
    }
    return jsonish_repr

  def get_size_in_mem(self):
    total = sys.getsizeof(self)
    total += sys.getsizeof(self.suite_name)
    total += sys.getsizeof(self.variant)
    variant_def = getattr(self.variant, 'def')
    if variant_def is not None:
      total += sys.getsizeof(variant_def)
      for key in variant_def:
        total += sys.getsizeof(key)
        total += sys.getsizeof(variant_def[key])
    total += sys.getsizeof(self.variant_hash)
    total += sys.getsizeof(self.total_tests_ran)
    # The elements in the unexpected_*_tests sets contain test names, whose
    # strings' sizes are already captured as part of the elements in all_tests.
    # So only count the size of their sets.
    total += sys.getsizeof(self.unexpected_passing_tests)
    total += sys.getsizeof(self.unexpected_failing_tests)
    total += sys.getsizeof(self.unexpected_skipped_tests)
    # Both keys and vals of individual_unexpected_test_by_test_name are already
    # captured in size reporting elsewhere. So only capture the size of the dict
    # here.
    total += sys.getsizeof(self.individual_unexpected_test_by_test_name)
    total += sys.getsizeof(self.all_tests)
    for t in self.all_tests:
      total += t.get_size_in_mem()
    # Let the RDBResults account for test_id_prefix since it can de-dupe
    # repeats.
    return total


@attrs()
class RDBResults:
  """Captures all results reported by RDB for all suites in a build's phase.

  Wraps a collection of RDBPerSuiteResults instances, each of which wrap
  a collections of RDBPerIndividualTestResults instances.

  Relationship looks something like the following, with each arrow representing
  a "one to many" relationship:
   ------------      --------------------      -----------------------------
  | RDBResults | -> | RDBPerSuiteResults | -> | RDBPerIndividualTestResults |
   ------------      --------------------      -----------------------------

  There should be one RDBResults for each phase of the build (eg: "with patch",
  "without patch", etc.) constructed higher up in the recipe stack.
  """

  # NOTE: If you add an attribute here, make sure to reflect the change in
  # update get_size_in_mem() below.
  all_suites = attrib(sequence[RDBPerSuiteResults])
  unexpected_failing_suites = attrib(sequence[RDBPerSuiteResults])

  @classmethod
  def create(cls, results):
    all_suites = []
    unexpected_failing_suites = []

    for res in results:
      assert isinstance(res, RDBPerSuiteResults)
      all_suites.append(res)
      if res.unexpected_failing_tests or res.invalid:
        unexpected_failing_suites.append(res)

    return cls(all_suites, unexpected_failing_suites)

  def to_jsonish(self):
    jsonish_repr = {
      'unexpected_failing_suites': [
        s.suite_name for s in self.unexpected_failing_suites
      ],
      'all_suites': [s.to_jsonish() for s in self.all_suites],
    }
    return jsonish_repr

  def get_size_in_mem(self):
    total = sys.getsizeof(self)
    total += sys.getsizeof(self.all_suites)
    seen_prefix_ids = set()
    for s in self.all_suites:
      total += s.get_size_in_mem()
      # A suite's test_id_prefix may or may not be unique across all suites.
      if id(s.test_id_prefix) not in seen_prefix_ids:
        total += sys.getsizeof(s.test_id_prefix)
      seen_prefix_ids.add(id(s.test_id_prefix))
    # self.unexpected_failing_suites is a subset of self.all_suites, so no need
    # to count the size of the elements in self.unexpected_failing_suites.
    total += sys.getsizeof(self.unexpected_failing_suites)
    return total

  def get_size_details(self):
    """Returns a human-readable description of this object's mem footprint.

    Returns: tuple of (total size of this object, list of human-readable lines)
    """

    def hr_size(size):
      for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1000.0:
          break
        size /= 1000.0
      return f'{size:.2f} {unit}'

    total_size_hr = hr_size(self.get_size_in_mem())
    lines = []
    lines.append('Size of this RDBResults: {}'.format(total_size_hr))
    for suite in self.all_suites:
      lines.append('')
      lines.append(
        '\tSize of RDBPerSuiteResults for {}: {}'.format(
          suite.suite_name, hr_size(suite.get_size_in_mem())
        )
      )
      lines.append(
        '\t\tNumber of RDBPerIndividualTestResults entries: {}'.format(
          len(suite.all_tests)
        )
      )
      lines.append(
        '\t\tSize of all RDBPerIndividualTestResults entries: {}'.format(
          hr_size(sum(t.get_size_in_mem() for t in suite.all_tests))
        )
      )

    return total_size_hr, lines


@attrs()
class IntervalStats:
  """Wraps IntervalStats instance

  # Each interval currently represents 24 weekday hours, including weekend
  # hours, if applicable.
  # Because intervals represent 24 weekday hours, interval_age = 1 would be the
  # last 24 hours of weekday data before the time the query is made,
  # interval_age=2 would be the 24 hours of data before that, and so on.
  # For more info, see the
  # go/src/go.chromium.org/luci/analysis/proto/v1/test_variants.proto in
  # infra/infra.
  """

  interval_age = attrib(int)
  # The number of verdicts which had only expected runs.
  # An expected run is a run (e.g. swarming task) which has at least
  # one expected result, excluding skipped results.
  total_run_expected_verdicts = attrib(int, default=0)
  # The number of verdicts which had both expected and
  # unexpected runs.
  total_run_flaky_verdicts = attrib(int, default=0)
  # The number of verdicts which had only unexpected runs.
  # An unexpected run is a run (e.g. swarming task) which had only
  # unexpected results (and at least one unexpected result),
  # excluding skips.
  total_run_unexpected_verdicts = attrib(int, default=0)


@attrs()
class StabilityAnalysisPerSuite:
  """Wraps a list of IndividualTestStabilityAnalysis instances per test suite"""

  suite_name = attrib(str)
  # List of IndividualTestStabilityAnalysis
  stability_analysis_list = attrib(list)
  # List of failing test_ids, each correlating to an
  # IndividualTestStabilityAnalysis object in stability_analysis_list
  test_ids = attrib(set_[str])

  @classmethod
  def create(cls, suite_name, stability_analysis_list):
    test_ids = set(analysis.test_id for analysis in stability_analysis_list)
    return cls(
      suite_name=suite_name,
      stability_analysis_list=stability_analysis_list,
      test_ids=test_ids,
    )

  def append_stability_analysis(self, stability_analysis):
    """Append an IndividualTestStabilityAnalysis

    Will assert that there is not already a stability_analysis for the same test

    Args:
      failure_analysis: An IndividualTestStabilityAnalysis instance to add
    """
    test_id = stability_analysis.test_id
    assert_msg = 'Already have an IndividualTestStabilityAnalysis for {}'
    assert test_id not in self.test_ids, assert_msg.format(test_id)
    self.stability_analysis_list.append(stability_analysis)


@attrs()
class IndividualTestStabilityAnalysis:
  # Full test ID.
  # Consists of a suite prefix + the test_name
  # e.g. ninja://gpu:gpu_unittests/FeatureInfoTest.Basic/Service.0
  test_id = attrib(str)
  # Name of test
  # e.g. FeatureInfoTest.Basic/Service.0
  test_name = attrib(str)
  suite_name = attrib(str)
  variant_hash = attrib(str)

  failure_rate_is_met = attrib(bool)
  flake_rate_is_met = attrib(bool)

  run_flaky_verdicts_1wd = attrib(int)
  run_flaky_verdicts_12h = attrib(int)

  @classmethod
  def create(cls, stability_analysis, suite_name, test_name):
    """Wraps a TestVariantStabilityAnalysis instance for one individual test"""
    return cls(
      test_id=stability_analysis.test_id,
      test_name=test_name,
      suite_name=suite_name,
      variant_hash=stability_analysis.variant_hash,
      failure_rate_is_met=stability_analysis.failure_rate.is_met,
      flake_rate_is_met=stability_analysis.flake_rate.is_met,
      run_flaky_verdicts_1wd=(
        stability_analysis.flake_rate.run_flaky_verdicts_1wd
      ),
      run_flaky_verdicts_12h=(
        stability_analysis.flake_rate.run_flaky_verdicts_12h
      ),
    )
