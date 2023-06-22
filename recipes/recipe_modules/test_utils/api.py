# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import itertools
import traceback

from recipe_engine import recipe_api
from recipe_engine import util as recipe_util

from .util import GTestResults, RDBPerSuiteResults, RDBResults
from .util import IndividualTestFailureRateAnalysis, FailureRateAnalysisPerSuite
from google.protobuf import timestamp_pb2

from PB.go.chromium.org.luci.resultdb.proto.v1 import (test_result as
                                                       test_result_pb2)

from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.recipe_engine.json.api import JsonOutputPlaceholder


class GTestResultsOutputPlaceholder(JsonOutputPlaceholder):

  def result(self, presentation, test):
    ret = super().result(presentation, test)
    return GTestResults(ret)


class TestUtilsApi(recipe_api.RecipeApi):
  """This class helps run tests and parse results.

  Tests are run in [up to] three stages:
    * 'with patch'
    * 'retry shards with patch'
    * 'without patch'

  The first stage applies the patch and runs the tests. If this passes, we're
  finished. Assuming that tests fail or return invalid results, then we deapply
  the patch and try running the tests again. If the failures are the same, then
  this is an issue with tip of tree and we ignore the failures.

  Finally, we roll the checkout and reapply the patch, and then rerun the
  failing tests. This helps confirm whether the failures were flakes or
  deterministic errors.
  """

  # This magic string is depended on by other infra tools.
  INVALID_RESULTS_MAGIC = 'TEST RESULTS WERE INVALID'

  # Header for test failures that caused the build to fail.
  NEW_FAILURES_TEXT = 'Tests failed with patch, and caused build to fail:'

  # Header for ignored failures due to without patch.
  IGNORED_FAILURES_TEXT = (
      'Tests failed with patch, but ignored as they also fail without patch '
      '(see Test Results Tab for more info):')

  # Header for ignored failures due to that they're known to be flaky.
  IGNORED_FLAKES_TEXT = (
      'Tests failed with patch, but ignored as they are known to be flaky '
      '(see Test Results Tab for more info):')

  def __init__(self, properties, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._max_reported_failures = properties.max_reported_failures or 30

    # This flag provides a escape-hatch to disable the feature of exonerating
    # flaky failures and make everything fall back to original behaviors when
    # there is a bug or outage. When landing the CL that flips the switch, it
    # should be TBRed and No-tried to minimize the negative impact of an outage.
    self._should_exonerate_flaky_failures = True

    # Skip retrying if there are >= N test suites with test failures. This most
    # likely indicates a problem with the CL itself instead of being caused by
    # flaky tests when N is large.
    # 5 is chosen because it's a conscious tradeoff between CQ cycle time and
    # false rejection rate, and a study conducted in http://bit.ly/37CSyBA shows
    # that 90th percentile cycle time can be improved by 2.2% with a loss of
    # 0.15% false rejection rate.
    self._min_failed_suites_to_skip_retry = (
        properties.min_failed_suites_to_skip_retry or 5)

  def limit_failures(self, failures, limit=None):
    """Limit failures of a step to prevent large results JSON.

    Args:
      failures - An iterable containing the failures that resulted from some
                 step.
      limit - The maxmium number of failures to display in the results.
    Returns:
      A tuple containing 2 elements:
        1. The list of the subset of at most *limit* elements of *failures*,
           suitable for iterating over and indexing into structures that are
           indexed by the failure names.
        2. The list of failures suitable for including in the step's text. If
           *failures* contains more elements than *limit*, it will contain an
           element indicating the number of additional failures.
    """
    if limit is None:
      limit = self._max_reported_failures
    if len(failures) <= limit:
      return failures, failures
    overflow_line = '... %d more (%d total) ...' % (len(failures) - limit,
                                                    len(failures))
    # failures might be a set, which doesn't support slicing, so create a list
    # out of an islice so that only the elemnts we are keeping are copied
    limited_failures = list(itertools.islice(failures, limit))
    return limited_failures, limited_failures + [overflow_line]

  def present_gtest_failures(self, step_result, presentation=None):
    """Update a step result's presentation with details of gtest failures.

    If the provided step result contains valid gtest results, then the
    presentation will be updated to include information about the failing
    tests, including logs for the individual failures.

    The max_reported_failures property modifies this behavior by limiting the
    number of tests that will appear in the step text and have their logs
    included. If the limit is exceeded the step text will indicate the number
    of additional failures.

    Args:
      step_result - The step result that potentially contains gtest results.
      presentation - The presentation to update. If not provided or None, the
                     presentation of *step_result* will be updated.
    Returns:
      The gtest_results object if it is present in the step result, otherwise
      None.
    """
    r = getattr(step_result, 'test_utils', None)
    r = getattr(r, 'gtest_results', None)

    if r and r.valid:
      p = presentation or step_result.presentation

      def emit_log(f, prefix):
        # FIXME: We could theoretically split up each run more. This would
        # require some refactoring in util.py to store each individual run's
        # logs, which we don't do currently.
        log_name = '%s: %s (status %s)' % (prefix, f, ','.join(
            r.raw_results[f]))
        p.logs[log_name] = [
            "Test '%s' completed with the following status(es): '%s'" %
            (f, '\',\''.join(r.raw_results[f])),
            '\n',
            "Test '%s' had the following logs when run:\n" % f,
            '\n',
            '=' * 80 + '\n',
            '\n',
        ] + r.logs[f] + [
            '\n',
            '=' * 80,
        ]

      deterministic_failures_set = set(r.deterministic_failures)
      flaky_failures_set = set(r.unique_failures) - deterministic_failures_set
      non_notrun_failures_set = {
          f for f in deterministic_failures_set
          if set(r.raw_results[f]) != {'NOTRUN'}
      }

      # If the deterministic_failures_set has other state of failures other
      # than NOTRUN, only report those failures and skip reporting the NOTRUN
      # failures as they will probably not be the 'actual' failures
      if non_notrun_failures_set:
        deterministic_failures_set = non_notrun_failures_set

      deterministic_failures, deterministic_failures_text = (
          self.limit_failures(sorted(deterministic_failures_set)))
      flaky_failures, flaky_failures_text = (
          self.limit_failures(sorted(flaky_failures_set)))
      for f in deterministic_failures:
        emit_log(f, 'Deterministic failure')
      for f in flaky_failures:
        emit_log(f, 'Flaky failure')

      p.step_text += self.m.presentation_utils.format_step_text([
          [
              'deterministic failures [caused step to fail]:',
              deterministic_failures_text
          ],
          ['flaky failures [ignored]:', flaky_failures_text],
      ])
    return r

  def _suite_exceed_allowed_failure_rate(self, suite, suffix: str) -> bool:
    """A helper to check if test failures exceed suite's expectation.

    Args:
        suite: steps.Test object
        suffix: string suffix designating test variant to pay attention to
    Returns:
        A boolean value indicating whether the number of test failures exceeds
          the allowed failure rate.
    """
    if not suite.spec.allowed_failure_percentage:
      return True
    return (len(suite.failures(suffix)) /
            len(suite.get_rdb_results(suffix).all_tests)
           ) * 100 > suite.spec.allowed_failure_percentage

  def _retrieve_bad_results(self, suites, suffix):
    """Extract invalid and failed suites from a list of suites.

    Args:
        suites: List of steps.Test objects to inspect
        suffix: string suffix designating test variant to pay attention to
    Returns:
      Tuple; (list of suites which were invalid; list of suites which failed)
    """
    # TODO: Refactor this/its subroutines to use resultsdb, not a O(n^2) lookup
    failed_test_suites = []
    invalid_results = []
    for t in suites:
      # Note that this is technically O(n^2). We expect n to be small.
      if not t.has_valid_results(suffix):
        invalid_results.append(t)
      elif t.deterministic_failures(suffix) and t not in failed_test_suites:
        if not self._suite_exceed_allowed_failure_rate(t, suffix):
          s = self.m.step.empty(f'Skip the failure of {t.name}')
          step_text = ('Allowed failure percentage: '
                       f'{t.spec.allowed_failure_percentage}%\n')
          step_text += 'Deterministic failures: '
          step_text += f'{len(t.deterministic_failures(suffix))}\n'
          step_text += f'Total: {len(t.get_rdb_results(suffix).all_tests)}\n'
          s.presentation.step_text = step_text
          continue
        failed_test_suites.append(t)
    return invalid_results, failed_test_suites

  def _create_groups(self, test_suites, sort_by_shard=False):
    """Creates test groups by checking item type in |test_suites|."""
    local_test_suites = []
    swarming_test_suites = []
    skylab_test_suites = []
    for t in test_suites:
      if t.runs_on_swarming:
        swarming_test_suites.append(t)
      elif t.is_skylabtest:
        skylab_test_suites.append(t)
      else:
        local_test_suites.append(t)

    if sort_by_shard:
      # Trigger tests which have a large number of shards earlier. They usually
      # take longer to complete, and triggering take a few minutes, so this
      # should get us a few extra minutes of speed.
      swarming_test_suites.sort(key=lambda t: -t.shards)

    groups = [
        LocalGroup(local_test_suites, self.m.resultdb),
        SwarmingGroup(swarming_test_suites, self.m.resultdb),
        SkylabGroup(skylab_test_suites, self.m.resultdb),
    ]
    return groups

  def run_tests_once(self, test_suites, suffix, sort_by_shard=False):
    """Runs a set of tests once. Used as a helper function by run_tests.

    Args:
      test_suites - list of steps.Test objects representing tests to run
      suffix - string specifying the stage/type of run, e.g. "without patch" or
        "retry (with patch)".
      sort_by_shard - if True, trigger tests in descending order by number of
        shards required to run the test. Performance optimization.

    Returns:
      rdb_results: util.RDBResults instance for test results as reported by RDB
      invalid suites, list of test_suites which were malformed or otherwise
          aborted
      failed suites, list of test_suites which failed in any way. Superset of
          invalids
    """
    groups = self._create_groups(test_suites, sort_by_shard)

    nest_name = 'test_pre_run (%s)' % suffix if suffix else 'test_pre_run'

    with self.m.step.nest(nest_name):
      for group in groups:
        group.pre_run(self.m, suffix)

    for group in groups:
      group.run(self.m, suffix)

    all_rdb_results = []
    for t in test_suites:
      if t.get_rdb_results(suffix):
        all_rdb_results.append(t.get_rdb_results(suffix))

    rdb_results = RDBResults.create(all_rdb_results)
    # Serialize the recipe's internal representation of its test results to a
    # log. To be used only for debugging.
    step_result = self.m.step(
        '$debug - all results%s' % ('' if not suffix else ' (%s)' % suffix),
        cmd=None)
    step_result.presentation.logs['serialized results'] = (
        self.m.json.dumps(rdb_results.to_jsonish(), indent=2).splitlines())
    total_size, mem_usage_lines = rdb_results.get_size_details()
    step_result.presentation.step_text = total_size
    step_result.presentation.logs['memory usage'] = mem_usage_lines

    bad_results_dict = {}
    (bad_results_dict['invalid'],
     bad_results_dict['failed']) = self._retrieve_bad_results(
         test_suites, suffix)

    return rdb_results, bad_results_dict['invalid'], bad_results_dict['failed']

  def run_tests_for_flake_endorser(self, test_objects_by_suffix):
    """Runs tests flake endorser test reruns.

    RDB results and failed/invalid test_suites isn't returned because flake
    rates will be analyzed with rdb results stored in test objects.

    Args:
      test_objects_by_suffix: A mapping from test suffixes to lists of
        steps.Test objects.
    """
    suffixes = sorted(test_objects_by_suffix.keys())
    groups_by_suffix = {}
    for suffix in suffixes:
      groups = self._create_groups(test_objects_by_suffix[suffix])
      groups_by_suffix[suffix] = groups

      nest_name = 'test_pre_run (%s)' % suffix
      with self.m.step.nest(nest_name):
        for group in groups:
          group.pre_run(self.m, suffix)

    for suffix in suffixes:
      groups = groups_by_suffix[suffix]
      for group in groups:
        group.run(self.m, suffix)

  def luci_milo_test_results_url(self, invocation_id):
    """Returns a url to the 'test results' tab in Milo.

    invocation_id (str): Full invocation ID that includes the swarming server
      and swarming task ID. e.g. task-chromium-swarm.appspot.com-5e052f430ead411

    Returns:
      str
    """
    return 'https://ci.chromium.org/ui/inv/{}/test-results'.format(
        invocation_id)

  def _exonerate_unrelated_failures(self, test_suites, suffix):
    """Notifies RDB of any unexpected test failure that doesn't fail the build.

    This includes any type of failure during the 'without patch' phase and any
    test failure that FindIt is tracking as flaky.

    For more details, see http://go/resultdb-concepts#test-exoneration

    Args:
      test_suites: list of steps.Test objects representing tests to run
      suffix: suffix indicating context of the test run
    """
    exonerations = []
    for suite in test_suites:
      results = suite.get_rdb_results(suffix)
      # Any failures in experimental suites should be exonerated.
      if suite.is_experimental:
        explanation_html = (
            'The test is marked as experimental, meaning any failures will '
            'not fail the build.')
        for t in results.unexpected_failing_tests:
          exonerations.append(
              test_result_pb2.TestExoneration(
                  test_id=t.test_id,
                  variant_hash=results.variant_hash,
                  explanation_html=explanation_html,
                  reason=test_result_pb2.ExonerationReason.NOT_CRITICAL,
              ))
      elif suffix == 'without patch':
        # Most unexpected failures in the "without patch" phase should be
        # exonerated. Tests that are skipped should _not_ be exonerated.
        # Unexpected passes should be already exonerated in tasks.
        # Keep this logic in-sync with
        # https://source.chromium.org/chromium/chromium/tools/build/+/main:recipes/recipe_modules/chromium_tests/steps.py;drc=137053ea;l=907
        # until that can be removed in favor of RDB.
        explanation_html = (
            'The test failed in both (with patch) and (without patch) steps, '
            'so the CL is exonerated for the test failures.'
        )

        # results.unexpected_failing_tests contains tests that resulted in
        # any unexpected non-PASS status (so FAIL, SKIP, CRASH). If the status
        # is SKIP, don't record it as an exoneration.
        for t in (results.unexpected_failing_tests -
                  results.unexpected_skipped_tests):
          test_results_link = self.luci_milo_test_results_url(t.invocation_id)
          updated_html = explanation_html + (
              '<br><a href="{}" target="_blank">Test results without patch</a>'
          ).format(test_results_link)
          exonerations.append(
              test_result_pb2.TestExoneration(
                  test_id=t.test_id,
                  variant_hash=results.variant_hash,
                  explanation_html=updated_html,
                  reason=test_result_pb2.ExonerationReason.OCCURS_ON_MAINLINE,
              ))
      # Any failure known to be flaky should also be exonerated.
      elif suffix == 'with patch':
        flakes = suite.known_luci_analysis_flaky_failures
        for known_flake in flakes:
          exonerations.append(
              test_result_pb2.TestExoneration(
                  test_id=(results.individual_unexpected_test_by_test_name[
                      known_flake].test_id),
                  variant_hash=results.variant_hash,
                  # TODO(crbug.com/1076096): add deep link to the Milo UI to
                  #  display the exonerated test results.
                  explanation_html=(
                      'LUCI Analysis reported this test as being flaky or '
                      'failing.'),
                  reason=test_result_pb2.ExonerationReason.OCCURS_ON_OTHER_CLS,
              ))

    if exonerations:
      self.m.resultdb.exonerate(
          test_exonerations=exonerations,
          step_name='exonerate unrelated test failures',
      )

  def _clean_failed_suite_list(self, failed_test_suites):
    """Returns a list of failed suites with flaky-fails-only suites excluded.

    This does not modify the argument list.
    This feature is controlled by the should_exonerate_flaky_failures property,
    which allows switch on/off with ease and flexibility.

    Args:
      failed_test_suites ([steps.Test]): A list of failed test suites to check.

    Returns:
      Tuple([steps.Test]): A tuple containing two lists of unexonerated test
        suites: 1. for failed test suites with exonerated suites removed and
        2. any exonerated suites that should be retried anyway for LUCI Analysis
        purposes
    """
    # If *all* the deterministic failures are known flaky tests, a test suite
    # will not be considered failure anymore, and thus will not be retried.
    #
    # As long as there is at least one non-flaky failure, all the failed shards
    # will be retried, including those due to flaky failures. Existing APIs
    # don't allow associating test failures with shard indices. Such a
    # connection could be implemented, but the cost is high and return is not;
    # both for CQ cycle time and for overall resource usage, return is very low.
    if not self._should_exonerate_flaky_failures:
      return failed_test_suites, []  #pragma: nocover

    pruned_suites = failed_test_suites[:]
    exonerated_suites_to_retry = []
    self._query_and_mark_flaky_failures(pruned_suites)
    for t in failed_test_suites:
      if not t.known_luci_analysis_flaky_failures:
        continue

      if set(t.deterministic_failures('with patch')).issubset(
          t.known_luci_analysis_flaky_failures):
        pruned_suites.remove(t)

    for t in failed_test_suites:
      # If a test is barely considered flaky, we want to run  it again to
      # keep it from thrashing between being classified as flaky and non-flaky
      # effectively "cannibalizing" our data
      # Long-term, we're thinking of triggering the retry shards and not
      # having the build collect the results.
      if (t.weak_luci_analysis_flaky_failures):
        exonerated_suites_to_retry.append(t)

    if exonerated_suites_to_retry:
      to_log = [{
          'suite_name': t.name,
          'tests_being_retried': list(t.deterministic_failures('with patch')),
      } for t in exonerated_suites_to_retry]
      log_step = self.m.step.empty(
          'logging weak LUCI Analysis exonerations',
          log_text=self.m.json.dumps(to_log, indent=2))
      log_step.presentation.properties['weetbix.retry_weak_exonerations'] = (
          to_log)

    return pruned_suites, exonerated_suites_to_retry

  def _query_luci_analysis_failures(self, tests_to_check):
    """Get flaky or deterministically failing tests by querying LUCI Analysis

    Exoneration criteria:
    - At least 3 flaky verdicts in the past 5 weekdays AND at least 1 flaky
    verdict in the past 1 weekday.
    OR
    - At least 7 recent unexpected verdicts
    (Flaky verdict means that in a single try build, the test failed the first
    run and then passed in the next run (aka the "retry shard (with patch)")

    If a suite has tests to be exonerated, the suite's
    _known_luci_analysis_flaky_failures will be updated.

    If a test is skipped, it will not be exonerated and therefore not included
    in the suite's _known_luci_analysis_flaky_failures.

    Args:
      tests_to_check (List(Test)): List of failing test suites

    """
    if not tests_to_check:
      return

    query_failure_rate_step_error_msg = None
    total_failing_variants = sum([
        len(tests.get_rdb_results('with patch').unexpected_failing_tests)
        for tests in tests_to_check
    ])
    if total_failing_variants > 100:
      self.m.step.empty(
          'Skipping querying LUCI Analysis for failure rates',
          step_text='Too many failed tests: %d' % total_failing_variants)
      return

    test_variants_to_query = []
    test_info_for_zip = []
    for suite in tests_to_check:
      rdb_results = suite.get_rdb_results('with patch')
      failing_tests = rdb_results.unexpected_failing_tests
      for failing_test in failing_tests:
        test_variants_to_query.append({
            'testId': failing_test.test_id,
            'variantHash': rdb_results.variant_hash,
        })
        test_info_for_zip.append({
            'test_name': failing_test.test_name,
            'suite_name': suite.name,
        })
    failure_analysis_protos = []
    try:
      failure_analysis_protos = self.m.luci_analysis.query_failure_rate(
          test_variants_to_query)
    except self.m.step.StepFailure as f:
      # Don't fail the build if something's wrong with LUCI Analysis.
      # We'll just not exonerate any failing tests and log that this happened.
      query_failure_rate_step_error_msg = f.reason_message()

    suite_to_per_suite_analysis = {}
    assert len(test_info_for_zip) == len(failure_analysis_protos)
    for test_info, failure_analysis in zip(test_info_for_zip,
                                           failure_analysis_protos):
      suite_name = test_info['suite_name']
      indiv_failure_analysis = IndividualTestFailureRateAnalysis.create(
          failure_analysis=failure_analysis,
          suite_name=suite_name,
          test_name=test_info['test_name'],
      )

      if suite_name not in suite_to_per_suite_analysis:
        suite_to_per_suite_analysis[suite_name] = (
            FailureRateAnalysisPerSuite.create(suite_name,
                                               [indiv_failure_analysis]))
      else:
        suite_to_per_suite_analysis[suite_name].append_failure_analysis(
            indiv_failure_analysis)

    # Handles LUCI Analysis RPC server errors: 500s or response returned but no
    # data
    if not suite_to_per_suite_analysis:
      if not query_failure_rate_step_error_msg:
        query_failure_rate_step_error_msg = (
            'Missing failure rates from LUCI Analysis')
      log_step = self.m.step.empty(
          'error querying LUCI Analysis for failure rates',
          step_text=query_failure_rate_step_error_msg)
      # Set an output property so this is easily queryable
      log_step.presentation.properties['luci_analysis_query_error'] = True
      return

    flaky_verdict_example_log = {}
    luci_analysis_build_output = []
    for suite in tests_to_check:
      failure_rate_analysis_per_suite = suite_to_per_suite_analysis.get(
          suite.name)

      # Set of test_names
      tests_to_exonerate = set()
      rdb_results = suite.get_rdb_results('with patch')
      test_name_to_result = rdb_results.individual_unexpected_test_by_test_name
      for analysis in failure_rate_analysis_per_suite.failure_analysis_list:
        # Don't exonerate skipped tests
        if (test_name_to_result[analysis.test_name] in
            rdb_results.unexpected_skipped_tests):
          continue

        # Get the failures in the last 12 hours, up to 10
        twelve_hours_ago = timestamp_pb2.Timestamp(
            seconds=int(self.m.time.time() - (60 * 60 * 12)))

        failure_in_last_12_hours = False
        for example in analysis.run_flaky_verdict_examples:
          partition_time = timestamp_pb2.Timestamp()
          partition_time.FromJsonString(example['partitionTime'])

          if partition_time.ToSeconds() > twelve_hours_ago.ToSeconds():
            failure_in_last_12_hours = True
            break

        # Query over the last 5 weekdays and the last weekday
        stats = {
            'total_flaky':
                analysis.get_flaky_verdict_counts([1, 5]),
            'total_recent_unexpected':
                analysis.get_unexpected_recent_verdict_count(),
        }

        # To be exonerated for being flaky, the failing test needs to be flaky
        # in the last 12 hours AND the last 5 weekdays. Even if exonerated it
        # must strongly meet these requirements to avoid a retry
        strongly_meets_min_flakiness = (
            failure_in_last_12_hours and stats['total_flaky'][5] >= 4)

        # The "barely" meets criteria will exonerate but still launch a retry
        # to avoid data cannibalization
        barely_meets_min_flakiness = (
            (stats['total_flaky'][5] >= 3 and stats['total_flaky'][1] >= 1) and
            (stats['total_flaky'][5] == 3 or not failure_in_last_12_hours))

        meets_min_recent_unexpected = stats['total_recent_unexpected'] >= 7
        exonerated = False
        strongly_exonerated = False

        if meets_min_recent_unexpected or strongly_meets_min_flakiness:
          tests_to_exonerate.add(analysis.test_name)
          strongly_exonerated = True
          exonerated = True
        elif barely_meets_min_flakiness:
          tests_to_exonerate.add(analysis.test_name)
          exonerated = True

        if not meets_min_recent_unexpected and barely_meets_min_flakiness:
          suite.add_weak_luci_analysis_flaky_failure(analysis.test_name)

        # This is to output LUCI Analysis information in the build output
        # property and to add the flaky verdict examples as a log in a step
        to_log = stats
        to_log['variant_hash'] = analysis.variant_hash
        to_log['exonerated'] = exonerated
        to_log['strongly_exonerated'] = strongly_exonerated
        to_log['test_id'] = analysis.test_id
        luci_analysis_build_output.append(to_log)
        flaky_verdict_example_log[analysis.test_id] = (
            analysis.run_flaky_verdict_examples)

      if tests_to_exonerate:
        suite.add_known_luci_analysis_flaky_failures(tests_to_exonerate)

    # Log this so that recent flaky verdicts are visible in build
    query_luci_analysis_step = self.m.step.empty(
        'query LUCI Analysis for flaky tests',
        log_text=self.m.json.dumps(flaky_verdict_example_log, indent=2))
    query_luci_analysis_step.presentation.properties['luci_analysis_info'] = (
        luci_analysis_build_output)

  def _query_and_mark_flaky_failures(self, failed_test_suites):
    """Queries and marks failed tests that are already known to be flaky.

    This method updates |failed_test_suites| in place, which will be used to
    skip retrying known flaky failures. A considered alternative is to directly
    modify the set of deterministic failures, but wasn't chosen because it's
    more desirable to be able to distinguish a skipped/forgiven failure from a
    success so that they can be properly surfaced.

    This feature is controlled by the should_exonerate_flaky_failures property,
    which allows switch on/off with ease and flexibility.

    If the enable_weetbix_queries experiment is set, this will also query
    LUCI Analysis for test failure/flakiness stats via the
    _query_luci_analysis_failures() function. The flaky test_names will be
    stored in the test_suite's known_luci_analysis_flaky_failures property

    Args:
      failed_test_suites ([steps.Test]): A list of failed test suites to check.
    """
    if not failed_test_suites:
      return

    luci_analysis_tests_to_check = []
    for test_suite in failed_test_suites:
      if len(test_suite.deterministic_failures('with patch')) > 100:
        # Bail out if there are too many failed tests because:
        # 1. It's unlikely they're all legitimate flaky failures.
        # 2. Avoid overloading the service.
        continue

      luci_analysis_tests_to_check.append(test_suite)
    self._query_luci_analysis_failures(luci_analysis_tests_to_check)

  def _still_invalid_suites(self, old_invalid_suites, retried_invalid_suites):
    # For swarming test suites, if we have valid test results from one of
    # the runs of a test suite, then that test suite by definition doesn't
    # have invalid test results.
    # Non-swarming test suites don't get retried in 'retry shards with patch'
    # steps, so all invalid non-swarming suites are still invalid
    non_swarming_invalid_suites = [
        t for t in old_invalid_suites if not t.runs_on_swarming
    ]
    still_invalid_swarming_suites = list(
        set(old_invalid_suites).intersection(retried_invalid_suites))
    return still_invalid_swarming_suites + non_swarming_invalid_suites

  def _should_abort_tryjob(self, rdb_results, allowed_failing_suites):
    """Determines if the current recipe should skip its next retry phases.

    Args:
      rdb_results: util.RDBResults instance for test results as reported by RDB
      allowed_failing_suites: A list of suite names, whose failures did not
        exceed the expectations. Remove them from unexpected_failing_suites
        and do not retry them.
    Return:
      True if we should skip retries; False otherwise.
    """
    try:
      skip_retry_footer = self.m.tryserver.get_footer(
          self.m.tryserver.constants.SKIP_RETRY_FOOTER)
    except self.m.step.StepFailure:
      result = self.m.step('failure getting footers', [])
      result.presentation.status = self.m.step.WARNING
      result.presentation.logs['exception'] = traceback.format_exc()
    else:
      if skip_retry_footer:
        result = self.m.step('retries disabled', [])
        result.presentation.step_text = (
            '\nfooter {} disables suite-level retries'.format(
                self.m.tryserver.constants.SKIP_RETRY_FOOTER))
        return True

    unexpected = [
        x.suite_name
        for x in rdb_results.unexpected_failing_suites
        if x.suite_name not in allowed_failing_suites
    ]
    should_abort = len(unexpected) >= self._min_failed_suites_to_skip_retry
    if should_abort:
      result = self.m.step('abort retry', [])
      result.presentation.status = self.m.step.FAILURE
      result.presentation.step_text = (
          '\nskip retrying because there are >= {} test suites with test '
          'failures and it most likely indicates a problem with the CL. These '
          'suites being:\n{}'.format(self._min_failed_suites_to_skip_retry,
                                     '\n'.join(x for x in unexpected)))
    else:
      result = self.m.step('proceed with retry', [])
      result.presentation.step_text = (
          '\nfewer than {} failures, continue with retries'.format(
              self._min_failed_suites_to_skip_retry))

    return should_abort

  def _extract_retriable_suites(self, failed_suites, invalid_suites,
                                exonerated_suites_to_retry, retry_failed_shards,
                                retry_invalid_shards):
    target_suites = set(exonerated_suites_to_retry)
    if retry_failed_shards:
      target_suites.update(failed_suites)
    if retry_invalid_shards:
      target_suites.update(invalid_suites)
    # Only Swarming suites can be usefully retried
    return [t for t in target_suites if t.runs_on_swarming]

  def run_tests(self,
                test_suites,
                suffix,
                sort_by_shard=False,
                retry_failed_shards=False,
                retry_invalid_shards=False):
    """Runs a list of test suites and returns the failed ones.

    If retry_[failed|invalid]_shards is true, this method retries shards that
    have deterministic failures or invalid results. Additionally, if the
    |_should_exonerate_flaky_failures| property is true, this method skips
    retrying deterministic failures that are already known to be flaky on ToT.

    Args:
      test_suites - iterable of objects implementing the steps.Test interface.
      suffix - custom suffix, e.g. "with patch", "without patch" indicating
               context of the test run
      sort_by_shard - if True, trigger tests in descending order by number of
        shards required to run the test. Performance optimization.
      retry_failed_shards: If true, attempts to retry failed shards of swarming
                           tests, typically used with retry_invalid_shards.
      retry_invalid_shards: If true, attempts to retry shards of swarming tests
                            without valid results.
    Returns:
      A tuple of (list of test suites with invalid results,
                  list of test suites which failed including invalid results)
    """
    if not self.m.resultdb.enabled:
      self.m.step.empty(
          'resultdb not enabled',
          status=self.m.step.FAILURE,
          step_text=('every build supported by chromium recipe code'
                     ' must have resultdb enabled'))
    rdb_results, invalid_test_suites, failed_test_suites = (
        self.run_tests_once(test_suites, suffix, sort_by_shard=sort_by_shard))

    _allowed_failing_suites = [
        x.name
        for x in test_suites
        if not self._suite_exceed_allowed_failure_rate(x, suffix)
    ]
    if self.m.tryserver.is_tryserver and self._should_abort_tryjob(
        rdb_results, allowed_failing_suites=_allowed_failing_suites):
      return invalid_test_suites, invalid_test_suites + failed_test_suites

    exonerated_suites_to_retry = []
    if suffix == 'with patch':
      failed_test_suites, exonerated_suites_to_retry = (
          self._clean_failed_suite_list(failed_test_suites))

    # If we encounter any unexpected test results that we believe aren't due to
    # the CL under test, inform RDB of these tests so it keeps a record.
    self._exonerate_unrelated_failures(test_suites, suffix)

    failed_and_invalid_suites = list(
        set(failed_test_suites + invalid_test_suites))

    if not (retry_failed_shards or retry_invalid_shards):
      return invalid_test_suites, failed_and_invalid_suites

    swarming_test_suites = self._extract_retriable_suites(
        failed_test_suites, invalid_test_suites, exonerated_suites_to_retry,
        retry_failed_shards, retry_invalid_shards)
    if not swarming_test_suites:
      return invalid_test_suites, failed_and_invalid_suites

    retry_suffix = 'retry shards'
    if suffix:
      retry_suffix += ' ' + suffix
    _, new_swarming_invalid_suites, _ = self.run_tests_once(
        swarming_test_suites, retry_suffix, sort_by_shard=True)

    invalid_test_suites = self._still_invalid_suites(
        old_invalid_suites=invalid_test_suites,
        retried_invalid_suites=new_swarming_invalid_suites)

    # Some suites might be passing now, since we retried some tests. Remove
    # any suites which are now fully passing.
    # failures_including_retry accounts for flaky tests and for both runs
    def _still_failing(suite):
      valid, failures = suite.failures_including_retry(suffix)
      return (not valid) or failures

    # Don't check the exonerated_suites_to_retry since they've already been
    # exonerated but have just been retried for luci analysis's sake (see
    # _clean_failed_suite_list() for more details).
    failed_and_invalid_suites = [
        t for t in failed_and_invalid_suites if _still_failing(t)
    ]

    return invalid_test_suites, failed_and_invalid_suites

  def run_tests_with_patch(self,
                           test_suites,
                           retry_failed_shards=False):
    """Runs tests and returns failures.

    Args:
      test_suites - iterable of objects implementing the steps.Test interface.
      retry_failed_shards: If true, attempts to retry failed shards of swarming
                           tests.

    Returns: A tuple (invalid_test_suites, all_failing_test_suites).
      invalid_test_suites: Test suites that do not have valid test results.
      all_failing_test_suites:
          This includes test suites that ran but have failing tests, test suites
          that do not have valid test results, and test suites that failed with
          otherwise unspecified reasons. This is a superset of
          invalid_test_suites.
    """
    invalid_test_suites, all_failing_test_suites = self.run_tests(
        test_suites,
        'with patch',
        sort_by_shard=True,
        retry_failed_shards=retry_failed_shards,
        retry_invalid_shards=retry_failed_shards)

    # Set metadata about invalid test suites.
    for t in invalid_test_suites:
      self._invalid_test_results(t)

    return (invalid_test_suites, all_failing_test_suites)

  def _invalid_test_results(self, test):
    """Marks test results as invalid.

    If |fatal| is True, emits a failing step. Otherwise emits a succeeding step.
    """
    self.m.tryserver.set_invalid_test_results_tryjob_result()

    # Record a step with INVALID_RESULTS_MAGIC, which chromium_try_flakes uses
    # for analysis.
    self.m.step.empty(test.name, step_text=self.INVALID_RESULTS_MAGIC)

  # TODO(crbug/1314194): Refactor ignored_failures and ignored_flakes to take
  # into account for ignored ToT failures exonerated by luci analysis.
  def _summarize_new_and_ignored_failures(self, test_suite, new_failures,
                                          ignored_failures, ignored_flakes,
                                          new_failures_text,
                                          ignored_failures_text,
                                          ignored_flakes_text):
    """Summarizes new and ignored failures and flakes in the test_suite.

    Args:
      test_suite: A test suite that's been retried.
      new_failures: Failures that are potentially caused by the patched CL.
      ignored_failures: Failures that are not caused by the patched CL.
      ignored_flakes: Failures due to known flakes or deterministic failures
        unrelated to the patched CL.
      new_failures_text: A user-visible string describing new_failures.
      ignored_failures_text: A user-visible string describing ignored_failures.
      ignored_flakes_text: A user-visible string describing ignored_flakes.

    Returns:
      A Boolean describing whether the retry succeeded. Which is to say, the
      patched CL did not cause the test suite to have deterministically failing
      tests.
    """
    new_failures = sorted(new_failures)
    ignored_failures = sorted(ignored_failures)
    ignored_flakes = sorted(ignored_flakes)

    suffix = 'test results summary'
    # There can be multiple blink_web_tests or blink_wpt_tests steps. Archive
    # the summaries to different files.
    if test_suite.spec.results_handler_name == 'layout tests':
      dest_file = '%s_%s.json' % (test_suite.canonical_name,
                                  suffix.replace(' ', '_'))
      self._archive_test_results_summary(
          {
              'failures': new_failures,
              'ignored': ignored_failures
          }, dest_file)

    step_name = '%s (%s)' % (test_suite.name, suffix)
    _, truncated_new_failures = self.limit_failures(new_failures)
    _, truncated_ignored_failures = self.limit_failures(ignored_failures)
    _, truncated_ignored_flakes = self.limit_failures(ignored_flakes)
    step_text = self.m.presentation_utils.format_step_text(
        [[new_failures_text, truncated_new_failures],
         [ignored_failures_text, truncated_ignored_failures],
         [ignored_flakes_text, truncated_ignored_flakes]])

    if ignored_flakes:
      step_text += ('<br/>If the mentioned known flaky tests are incorrect, '
                    'please file a bug at: http://bit.ly/37I61c2<br/>')

    result = self.m.step.empty(step_name, step_text=step_text)
    if new_failures:
      result.presentation.logs[
          'failures caused build to fail'] = self.m.json.dumps(
              new_failures, indent=2).split('\n')
    if ignored_failures:
      result.presentation.logs[
          'failures ignored as they also fail without patch'] = (
              self.m.json.dumps(ignored_failures, indent=2).split('\n'))
    if ignored_flakes:
      ignored_flakes_key = ('failures ignored as they are known to be flaky or '
                            'deterministically failing')

      result.presentation.logs[ignored_flakes_key] = self.m.json.dumps(
          ignored_flakes, indent=2).split('\n')

    if new_failures:
      result.presentation.status = self.m.step.FAILURE
      self.m.tryserver.set_test_failure_tryjob_result()
    elif ignored_failures or ignored_flakes:
      result.presentation.status = self.m.step.WARNING

    return not bool(new_failures)

  def summarize_test_with_patch_deapplied(self, test_suite):
    """Summarizes test results after a CL has been retried with patch deapplied.

    Args:
      If there are no new failures, this method will emit a passing step.
      If there are new failures, this method will emit a step whose presentation
      status is 'FAILURE'.

    Returns:
      A Boolean describing whether the retry succeeded. Which is to say, all
      tests that failed in the original run also failed in the retry, which
      suggests that the error is due to an issue with top of tree, and should
      not cause the CL to fail.
    """
    valid_results, ignored_failures = (
        test_suite.without_patch_failures_to_ignore())
    if not valid_results:
      self._invalid_test_results(test_suite)
      result = self.m.step.empty(
          '%s (test results summary)' % test_suite.name,
          step_text=('\n%s (without patch) did not produce valid results. '
                     'The suite likely reported no failed tests but exited '
                     'non-zero.') % test_suite.name)
      result.presentation.status = self.m.step.FAILURE
      self.m.tryserver.set_test_failure_tryjob_result()
      return False

    valid_results, failures = test_suite.with_patch_failures_including_retry()
    assert valid_results, (
        "If there were no valid results, then there was no "
        "point in running 'without patch'. This is a recipe bug.")

    # The FAILURE and NOTRUN test statuses are both considered deterministic
    # failures. But some suites can have trouble during later phases, causing
    # some tests that exited with FAILURE in the 'with patch' phase to exit
    # with NOTRUN in the 'without patch' phase. So when a 'without patch' test
    # fails with a different status, don't ignore it.
    if ignored_failures:
      with_patch_notruns = test_suite.notrun_failures('with patch')
      without_patch_notruns = test_suite.notrun_failures('without patch')
      for ignored_failure in ignored_failures.copy():
        if ((ignored_failure in with_patch_notruns) !=
            (ignored_failure in without_patch_notruns)):
          ignored_failures.remove(ignored_failure)

    new_failures = failures - ignored_failures

    return self._summarize_new_and_ignored_failures(
        test_suite, new_failures, ignored_failures,
        test_suite.known_luci_analysis_flaky_failures, self.NEW_FAILURES_TEXT,
        self.IGNORED_FAILURES_TEXT, self.IGNORED_FLAKES_TEXT)

  def summarize_failing_test_with_no_retries(self, test_suite):
    """Summarizes a failing test suite that is not going to be retried."""
    valid_results, new_failures = (
        test_suite.with_patch_failures_including_retry())

    if not valid_results:  # pragma: nocover
      self.m.step.empty(
          '{} assertion'.format(test_suite.name),
          status=self.m.step.INFRA_FAILURE,
          step_text=(
              'This line should never be reached.'
              ' If a test has invalid results and is not going to be retried,'
              ' then a failing step should have already been emitted.'))

    return self._summarize_new_and_ignored_failures(
        test_suite,
        new_failures,
        set(),
        test_suite.known_luci_analysis_flaky_failures,
        new_failures_text=self.NEW_FAILURES_TEXT,
        ignored_failures_text=self.IGNORED_FAILURES_TEXT,
        ignored_flakes_text=self.IGNORED_FLAKES_TEXT)

  def _archive_test_results_summary(self, test_results_summary, dest_filename):
    """Archives the test results summary as JSON, storing it alongside the
    results from the first run."""
    cmd = [
        'python3',
        self.resource('archive_layout_test_results_summary.py'),
        '--test-results-summary-json',
        self.m.json.input(test_results_summary),
        '--build-number',
        self.m.buildbucket.build.number,
        '--builder-name',
        self.m.buildbucket.builder_name,
        '--gs-bucket',
        'gs://chromium-layout-test-archives',
        '--dest-filename',
        dest_filename,
    ]
    cmd += self.m.build.bot_utils_args
    self.m.step('archive_test_results_summary', cmd)

  @recipe_util.returns_placeholder
  def gtest_results(self, add_json_log=True, leak_to=None):
    """A placeholder which will expand to
    '--test-launcher-summary-output=/tmp/file'.

    Provides the --test-launcher-summary-output flag since --flag=value
    (i.e. a single token in the command line) is the required format.

    The test_results will be an instance of the GTestResults class.

    Visit
    https://source.chromium.org/chromium/infra/infra/+/main:recipes-py/README.recipes.md
    to find the definition and usage of add_json_log and leak_to.
    """
    return GTestResultsOutputPlaceholder(self, add_json_log, leak_to=leak_to)


class TestGroup:
  """Abstract class defines the shared interface for tests.

  Attributes:
    * test_suites - Iterable of objects implementing the steps.Test interface.
    * resultdb_api - Recipe API object for the resultdb recipe module.
  """

  def __init__(self, test_suites, resultdb_api=None):
    self._test_suites = test_suites
    self.resultdb_api = resultdb_api

  def pre_run(self, api, suffix):  # pragma: no cover
    """Executes the |pre_run| method of each test.

    Args:
      api - The api object of this module.
      suffix - The test name suffix.
    """
    raise NotImplementedError()

  def run(self, api, suffix):  # pragma: no cover
    """Executes the |run| method of each test.

    Args:
      api - The api object of this module.
      suffix - The test name suffix.
    """
    raise NotImplementedError()

  def _run_func(self, test_func, api, suffix):
    """Runs a function on a test, and handles errors appropriately."""
    try:
      test_func(suffix)
    except api.step.InfraFailure:
      raise
    except api.step.StepFailure:
      pass

  def include_rdb_invocation(self, suffix,
                             step_name='include test invocations'):
    invocation_names = []
    if self.resultdb_api and self.resultdb_api.enabled:
      for t in self._test_suites:
        invocation_names.extend(t.get_invocation_names(suffix))

      # Include the task invocations in the build's invocation.
      # Note that 'without patch' results are reported but not included in
      # the builds' invocation, since the results are not related to the
      # patch under test.
      if invocation_names and suffix != 'without patch':
        self.resultdb_api.include_invocations(
            self.resultdb_api.invocation_ids(invocation_names),
            step_name=step_name)

  def fetch_rdb_results(self,
                        test,
                        suffix,
                        flakiness_api,
                        force_fetch_all_results=False):
    """Queries RDB for the given test's results.

    If suite has allowed_failure_percentage configured, need to fetch all
    results, in spite of the input force_fetch_all_results, to calculate the
    failure rate.

    If Flake Endorser is enabled and the target result count is not too large
    (the limit is set in flakiness module), the method collects all results.
    Otherwise, the method collects only test results from variants that have
    unexpected results. |force_fetch_all_results| can be used to override Flake
    Endorser status and target result count limit.

    Args:
      test: steps.Test object for the given test.
      suffix: Test name suffix.
      flakiness_api: Recipe API object for the flakiness recipe module.
      force_fetch_all_results: If True, return all tests results. All results
        will be returned regardless if the test has `allowed_failure_percentage`
        specified or if Flake Endorser is enabled and the result sizes are below
        a limit.
    """
    if not test.is_enabled:
      res = RDBPerSuiteResults.create({},
                                      failure_on_exit=False,
                                      total_tests_ran=0,
                                      suite_name=test.canonical_name,
                                      test_id_prefix=test.test_id_prefix)
    elif not (invocation_names := test.get_invocation_names(suffix)):
      res = RDBPerSuiteResults.create({},
                                      failure_on_exit=True,
                                      total_tests_ran=0,
                                      suite_name=test.canonical_name,
                                      test_id_prefix=test.test_id_prefix)
    else:
      test_stats = self.resultdb_api.query_test_result_statistics(
          invocations=invocation_names, step_name='%s stats' % test.name)
      variants_with_unexpected_results = True
      if (force_fetch_all_results or test.spec.allowed_failure_percentage or
          (flakiness_api.check_for_flakiness and test_stats.total_test_results
           <= flakiness_api.PER_TEST_OBJECT_RESULT_LIMIT)):
        variants_with_unexpected_results = False
      # TODO(crbug.com/1366463): Add default test data for "check flakiness"
      # steps.
      unexpected_result_invocations = self.resultdb_api.query(
          inv_ids=self.resultdb_api.invocation_ids(invocation_names),
          variants_with_unexpected_results=variants_with_unexpected_results,
          limit=0,
          step_name='%s results' % test.name,
          tr_fields=RDBPerSuiteResults.NEEDED_FIELDS,
      )
      res = RDBPerSuiteResults.create(
          unexpected_result_invocations,
          suite_name=test.canonical_name,
          total_tests_ran=test_stats.total_test_results,
          allow_flaky_passes=suffix != 'without patch',
          failure_on_exit=test.failure_on_exit(suffix),
          test_id_prefix=test.test_id_prefix)
    test.update_rdb_results(suffix, res)


class LocalGroup(TestGroup):

  def __init__(self, test_suites, resultdb):
    super().__init__(test_suites, resultdb)

  def pre_run(self, api, suffix):
    """Executes the |pre_run| method of each test."""
    for t in self._test_suites:
      self._run_func(t.pre_run, api, suffix)

  def run(self, api, suffix):
    """Executes the |run| method of each test."""
    for t in self._test_suites:
      self._run_func(t.run, api, suffix)
      self.fetch_rdb_results(t, suffix, api.flakiness)

    self.include_rdb_invocation(
        suffix, step_name='include local test invocations')


class SwarmingGroup(TestGroup):

  def __init__(self, test_suites, resultdb):
    super().__init__(test_suites, resultdb)
    self._task_ids_to_test = {}

  def pre_run(self, api, suffix):
    """Executes the |pre_run| method of each test."""
    futures = []
    for t in self._test_suites:
      futures.append(api.futures.spawn_immediate(t.pre_run, suffix))
    for f in futures:
      f.result()

    for t in self._test_suites:
      if not t.is_enabled:
        continue
      task = t.get_task(suffix)

      task_ids = tuple(task.get_task_ids())
      self._task_ids_to_test[task_ids] = t

    self.include_rdb_invocation(
        suffix, step_name='include swarming task invocations')

  def run(self, api, suffix):
    """Executes the |run| method of each test."""
    for test in self._test_suites:
      if not test.is_enabled:
        self.fetch_rdb_results(test, suffix, api.flakiness)
        test.run(suffix)

    attempts = 0
    while self._task_ids_to_test:
      nest_name = 'collect tasks'
      if suffix:
        nest_name += ' (%s)' % suffix
      with api.step.nest(nest_name):
        finished_sets, attempts = (
            api.chromium_swarming.wait_for_finished_task_set(
                list(self._task_ids_to_test),
                suffix=((' (%s)' % suffix) if suffix else ''),
                attempts=attempts))
        for task_set in finished_sets:
          test = self._task_ids_to_test[tuple(task_set)]
          self.fetch_rdb_results(test, suffix, api.flakiness)

      for task_set in finished_sets:
        test = self._task_ids_to_test[tuple(task_set)]
        test.run(suffix)
        del self._task_ids_to_test[task_set]

    # Testing this suite is hard, because the step_test_data for get_states
    # means that it's hard to force it to never return COMPLETED for tasks. This
    # shouldn't happen anyways, so hopefully not testing this will be fine.
    if self._task_ids_to_test:  # pragma: no cover
      # Something weird is going on, just collect tasks like normal, and log a
      # warning.
      api.step.empty(
          'swarming tasks.get_states issue',
          status=api.step.WARNING,
          step_text=(
              'swarming tasks.get_states seemed to indicate that all tasks for'
              ' this build were finished collecting, but the recipe thinks the'
              ' following tests still need to be collected:\n%s'
              '\nSomething is probably wrong with the swarming server.'
              ' Falling back on the old collection logic.' %
              ', '.join(test.name for test in self._task_ids_to_test.values())))

      for test in self._task_ids_to_test.values():
        # We won't collect any already collected tasks, as they're removed from
        # self._task_ids_to_test
        self.fetch_rdb_results(test, suffix, api.flakiness)
        test.run(suffix)


class SkylabGroup(TestGroup):

  def __init__(self, test_suites, result_db):
    super().__init__(test_suites, result_db)
    self.ctp_builds_by_tag = {}
    self.ctp_build_timeout_sec = 3600

  def pre_run(self, api, suffix):
    """Schedule each Skylab test request to a CTP build."""
    tests = [t for t in self._test_suites if t.is_skylabtest and t.is_enabled]
    if tests:
      # Respect timeout of each test run by this CTP build.
      build_timeout = max([t.spec.timeout_sec for t in tests])
      if build_timeout > self.ctp_build_timeout_sec:
        self.ctp_build_timeout_sec = build_timeout
      self.ctp_builds_by_tag = api.skylab.schedule_suites(tests)

  def run(self, api, suffix):
    """Fetch the responses for each test request."""
    tag_resp = {}
    if self.ctp_builds_by_tag:
      tag_resp = api.skylab.wait_on_suites(
          self.ctp_builds_by_tag, timeout_seconds=self.ctp_build_timeout_sec)
    for t in self._test_suites:
      t.ctp_build_ids = self.ctp_builds_by_tag.get(t.name)
      t.test_runner_builds = tag_resp.get(t.name, {})
      # Skylab tests are executed by CrOS builders, which may retry upon
      # failures within their builds. So the same suffix may have multiple test
      # runs. We need to fetch all the results for a suffix, or the recipe
      # can not separate the flaky tests and deterministic failures.
      self.fetch_rdb_results(
          t, suffix, api.flakiness, force_fetch_all_results=True)
      self._run_func(t.run, api, suffix)

    self.include_rdb_invocation(
        suffix, step_name='include skylab_test_runner invocations')
