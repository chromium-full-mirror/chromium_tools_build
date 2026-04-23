# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections
import contextlib
import itertools
import traceback
from collections.abc import Iterable
from urllib.parse import urlencode

from recipe_engine import recipe_api
from recipe_engine import util as recipe_util
from recipe_engine.config_types import Path

from .util import GTestResults, RDBPerSuiteResults, RDBResults
from .util import IndividualTestStabilityAnalysis, StabilityAnalysisPerSuite
from google.protobuf import timestamp_pb2

from PB.go.chromium.org.luci.resultdb.proto.v1 import (test_exoneration as
                                                       test_exoneration_pb2)
from PB.go.chromium.org.luci.resultdb.proto.v1 import (test_result as
                                                       test_result_pb2)

from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.recipe_engine.json.api import JsonOutputPlaceholder

# The suffix to indicate a retry attempt.
_RETRY_SUFFIX = 'retry shards'


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

  def get_milo_test_results_url(self, test_name):
    """Returns a URL to the "Test Results" tab in Milo for the current build."""
    url = 'https://luci-milo.appspot.com/ui/inv/'
    inv_name = self.m.resultdb.current_invocation
    if inv_name.startswith('invocations/'):
      inv_name = inv_name[12:]
    return url + inv_name + '/test-results?' + urlencode({'q': test_name})

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
      return failures, [
          f'[{failure}]({self.get_milo_test_results_url(failure)})'
          for failure in failures
      ]
    overflow_line = '... %d more (%d total) ...' % (len(failures) - limit,
                                                    len(failures))
    # failures might be a set, which doesn't support slicing, so create a list
    # out of an islice so that only the elemnts we are keeping are copied
    limited_failures = list(itertools.islice(failures, limit))
    return limited_failures, [
        f'[{failure}]({self.get_milo_test_results_url(failure)})'
        for failure in limited_failures
    ] + [overflow_line]

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
      if t.is_experimental or not t.is_enabled:
        continue
      if not t.has_valid_results(suffix) or not t.did_complete(suffix):
        invalid_results.append(t)
      elif t.deterministic_failures(suffix) and t not in failed_test_suites:
        failed_test_suites.append(t)
    return invalid_results, failed_test_suites

  def _create_groups(self, test_suites, sort_by_shard=False):
    """Creates test groups by checking item type in |test_suites|."""
    # The dictionary is initialized with explicit keys to ensure that if a new
    # locality is added, it gets handled here
    test_suites_by_locality = {
        steps.TestLocality.LOCAL: [],
        steps.TestLocality.SWARMING: [],
        steps.TestLocality.SKYLAB: [],
    }
    for t in test_suites:
      test_suites_by_locality[t.locality].append(t)

    if sort_by_shard:
      # Trigger tests which have a large number of shards earlier. They usually
      # take longer to complete, and triggering take a few minutes, so this
      # should get us a few extra minutes of speed.
      test_suites_by_locality[steps.TestLocality.SWARMING].sort(
          key=lambda t: -t.shards)

    groups = [
        LocalGroup(test_suites_by_locality[steps.TestLocality.LOCAL],
                   self.m.resultdb),
        SwarmingGroup(test_suites_by_locality[steps.TestLocality.SWARMING],
                      self.m.resultdb),
        SkylabGroup(test_suites_by_locality[steps.TestLocality.SKYLAB],
                    self.m.resultdb),
    ]
    return groups

  def run_tests_once(self,
                     checkout_dir: Path,
                     source_dir: Path,
                     build_dir: Path,
                     test_suites,
                     suffix,
                     *,
                     sort_by_shard=False,
                     include_utr_instruction=False):
    """Runs a set of tests once. Used as a helper function by run_tests.

    Args:
      checkout_dir - The directory where the checkout was performed.
      source_dir - The path to the top-level repo.
      build_dir - Path to the build dir.
      test_suites - list of steps.Test objects representing tests to run
      suffix - string specifying the stage/type of run, e.g. "without patch" or
        "retry (with patch)".
      sort_by_shard - if True, trigger tests in descending order by number of
        shards required to run the test. Performance optimization.
      include_utr_instruction: Whether or not to include UTR reproduction
        instructions

    Returns:
      invalid suites, list of test_suites which were malformed or otherwise
          aborted
      failed suites, list of test_suites which failed in any way. Superset of
          invalids
    """
    groups = self._create_groups(test_suites, sort_by_shard)

    nest_name = 'test_pre_run (%s)' % suffix if suffix else 'test_pre_run'

    with self.m.step.nest(nest_name):
      for group in groups:
        group.pre_run(self.m, suffix, include_utr_instruction)

    # Update the instructions for the test result instructions
    if test_suites:
      self.m.repro_instructions.update_invocation_instructions(
          step_name=f'update invocation instructions {suffix}'
          if suffix else 'update invocation instructions')

    for group in groups:
      group.run(self.m, checkout_dir, source_dir, build_dir, suffix)

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

    return bad_results_dict['invalid'], bad_results_dict['failed']

  def run_tests_for_flake_endorser(self,
                                   checkout_dir: Path,
                                   source_dir: Path,
                                   build_dir: Path,
                                   test_objects_by_suffix,
                                   include_utr_instruction: bool = False):
    """Runs tests flake endorser test reruns.

    RDB results and failed/invalid test_suites isn't returned because flake
    rates will be analyzed with rdb results stored in test objects.

    Args:
      checkout_dir: The directory where the checkout was performed.
      source_dir: The path to the top-level repo.
      build_dir: Path to the build dir.
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
          group.pre_run(self.m, suffix, include_utr_instruction)

    for suffix in suffixes:
      groups = groups_by_suffix[suffix]
      for group in groups:
        group.run(self.m, checkout_dir, source_dir, build_dir, suffix)
    # Update the instructions for the test result instructions
    self.m.repro_instructions.update_invocation_instructions()

  def luci_milo_test_results_url(self, invocation_id):
    """Returns a url to the 'test results' tab in Milo.

    invocation_id (str): Full invocation ID that includes the swarming server
      and swarming task ID. e.g. task-chromium-swarm.appspot.com-5e052f430ead411

    Returns:
      str
    """
    return 'https://ci.chromium.org/ui/inv/{}/test-results'.format(
        invocation_id)

  def fetch_rdb_results(self,
                        test,
                        suffix,
                        test_invocation_names=None,
                        force_fetch_all_results=False):
    """Queries RDB for the given test's results.

    If Flake Endorser is enabled and the target result count is not too large
    (the limit is set in flakiness module), the method collects all results.
    Otherwise, the method collects only test results from variants that have
    unexpected results. |force_fetch_all_results| can be used to override Flake
    Endorser status and target result count limit.

    Args:
      test: steps.Test object for the given test.
      suffix: Test name suffix.
      resultdb_api: Recipe API object for the resultdb recipe module.
      flakiness_api: Recipe API object for the flakiness recipe module.
      test_invocation_names: Test invocation names to fetch test results. If not
        provided, will use from invocation names from input test.
      force_fetch_all_results: If True, return all tests results. All results
        will be returned regardless if Flake Endorser is enabled and the result
        sizes are below a limit.
    """
    if not test.is_enabled:
      res = RDBPerSuiteResults.create({},
                                      failure_on_exit=False,
                                      total_tests_ran=0,
                                      suite_name=test.canonical_name,
                                      test_id_prefix=test.test_id_prefix)
    elif not (invocation_names := test_invocation_names or
              test.get_invocation_names(suffix)):
      # If we can't find invocation names and resultdb is enabled for the test,
      # just mark its status as invalid.
      failure_on_exit = True
      total_tests_ran = 0
      if not test.spec.resultdb.enable:
        failure_on_exit = test.failure_on_exit(suffix)
        total_tests_ran = 1
      res = RDBPerSuiteResults.create({},
                                      failure_on_exit=failure_on_exit,
                                      total_tests_ran=total_tests_ran,
                                      suite_name=test.canonical_name,
                                      test_id_prefix=test.test_id_prefix)
    else:
      test_stats = self.m.resultdb.query_test_result_statistics(
          invocations=invocation_names, step_name='%s stats' % test.name)
      variants_with_unexpected_results = True
      if (force_fetch_all_results or
          (self.m.flakiness and self.m.flakiness.check_for_flakiness and
           test_stats.total_test_results
           <= self.m.flakiness.PER_TEST_OBJECT_RESULT_LIMIT)):
        variants_with_unexpected_results = False
      # TODO(crbug.com/1366463): Add default test data for "check flakiness"
      # steps.
      unexpected_result_invocations = self.m.resultdb.query(
          inv_ids=self.m.resultdb.invocation_ids(invocation_names),
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
              test_exoneration_pb2.TestExoneration(
                  test_id=t.test_id,
                  variant=results.variant,
                  explanation_html=explanation_html,
                  reason=test_exoneration_pb2.ExonerationReason.NOT_CRITICAL,
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
              test_exoneration_pb2.TestExoneration(
                  test_id=t.test_id,
                  variant=results.variant,
                  explanation_html=updated_html,
                  reason=test_exoneration_pb2.ExonerationReason
                  .OCCURS_ON_MAINLINE,
              ))
      # Any failure known to be flaky should also be exonerated.
      elif suffix == 'with patch':
        flakes = suite.known_luci_analysis_flaky_failures
        for known_flake in flakes:
          exonerations.append(
              test_exoneration_pb2.TestExoneration(
                  test_id=(results.individual_unexpected_test_by_test_name[
                      known_flake].test_id),
                  variant=results.variant,
                  # TODO(crbug.com/1076096): add deep link to the Milo UI to
                  #  display the exonerated test results.
                  explanation_html=(
                      'LUCI Analysis reported this test as being flaky or '
                      'failing.'),
                  reason=test_exoneration_pb2.ExonerationReason
                  .OCCURS_ON_OTHER_CLS,
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
    """Query LUCI Analysis for failure or stability rates

    Args:
      tests_to_check (List(Test)): List of failing test suites
    """
    if not tests_to_check:
      return

    total_failing_variants = sum(
        len(tests.get_rdb_results('with patch').unexpected_failing_tests)
        for tests in tests_to_check)
    if total_failing_variants > 100:
      self.m.step.empty(
          'Skipping querying LUCI Analysis for failure rates',
          step_text='Too many failed tests: %d' % total_failing_variants)
      return

    self._query_stability_rate(tests_to_check)

  def _query_stability_rate(self, tests_to_check):
    """Get flaky or deterministically failing tests by querying LUCI Analysis

    Exoneration criteria is defined in infra/config/luci-analysis.cfg in
    chromium/src with FailureRateCriteria and FlakeRateCriteria.

    If a suite has tests to be exonerated, the suite's
    _known_luci_analysis_flaky_failures will be updated.

    If a test is skipped, it will not be exonerated and therefore not included
    in the suite's _known_luci_analysis_flaky_failures.

    Args:
      tests_to_check (List(Test)): List of failing test suites

    """
    test_variants_to_query = []
    test_info_for_zip = []
    for suite in tests_to_check:
      rdb_results = suite.get_rdb_results('with patch')
      # The tests are saved as a set but we need consistent order for mock data
      for failing_test in sorted(rdb_results.unexpected_failing_tests):
        gitiles_commit = self.m.buildbucket.build.output.gitiles_commit
        sources = {
            'gitilesCommit': {
                'host': gitiles_commit.host,
                'project': gitiles_commit.project,
                'commitHash': gitiles_commit.id,
                'ref': gitiles_commit.ref,
                'position': gitiles_commit.position,
            },
            'changelists': [{
                'host': change.host,
                'project': change.project,
                'change': change.change,
                'patchset': change.patchset,
            } for change in self.m.buildbucket.build.input.gerrit_changes],
        }
        test_variants_to_query.append({
            'testId': failing_test.test_id,
            'variantHash': rdb_results.variant_hash,
            'sources': sources,
        })
        test_info_for_zip.append({
            'test_name': failing_test.test_name,
            'suite_name': suite.name,
        })
    stability_analysis_protos = []
    query_stability_rate_step_error_msg = None
    try:
      stability_analysis_protos, _ = self.m.luci_analysis.query_stability(
          test_variants_to_query)
    except self.m.step.StepFailure as f:
      # Don't fail the build if something's wrong with LUCI Analysis.
      # We'll just not exonerate any failing tests and log that this happened.
      # Handles LUCI Analysis RPC server errors: 500s
      query_stability_rate_step_error_msg = f.reason
      log_step = self.m.step.empty(
          'error querying LUCI Analysis for failure rates',
          step_text=query_stability_rate_step_error_msg)
      # Set an output property so this is easily queryable
      log_step.presentation.properties['luci_analysis_query_error'] = True
      return

    # There should be an analysis object for each queried test
    assert len(test_info_for_zip) == len(stability_analysis_protos)
    suite_to_per_suite_analysis = {}
    for test_info, stability_analysis in zip(test_info_for_zip,
                                             stability_analysis_protos):
      suite_name = test_info['suite_name']
      indiv_stability_analysis = IndividualTestStabilityAnalysis.create(
          stability_analysis=stability_analysis,
          suite_name=suite_name,
          test_name=test_info['test_name'],
      )

      if suite_name not in suite_to_per_suite_analysis:
        suite_to_per_suite_analysis[suite_name] = (
            StabilityAnalysisPerSuite.create(suite_name,
                                             [indiv_stability_analysis]))
      else:
        suite_to_per_suite_analysis[suite_name].append_stability_analysis(
            indiv_stability_analysis)

    luci_analysis_build_output = []
    for suite in tests_to_check:
      stability_analysis_per_suite = suite_to_per_suite_analysis.get(suite.name)

      # Set of test_names
      test_names_to_exonerate = set()
      rdb_results = suite.get_rdb_results('with patch')
      test_name_to_result = rdb_results.individual_unexpected_test_by_test_name
      for analysis in stability_analysis_per_suite.stability_analysis_list:
        # Don't exonerate skipped tests
        if (test_name_to_result[analysis.test_name]
            in rdb_results.unexpected_skipped_tests):
          continue

        exonerated = analysis.failure_rate_is_met or analysis.flake_rate_is_met
        if exonerated:
          test_names_to_exonerate.add(analysis.test_name)

        strongly_exonerated = analysis.failure_rate_is_met or (
            analysis.flake_rate_is_met and analysis.run_flaky_verdicts_12h > 0)

        if not strongly_exonerated and analysis.flake_rate_is_met:
          suite.add_weak_luci_analysis_flaky_failure(analysis.test_name)

        # This is to output LUCI Analysis information in the build output
        # property
        to_log = {
            'failure_rate_is_met': analysis.failure_rate_is_met,
            'flake_rate_is_met': analysis.flake_rate_is_met,
            'variant_hash': analysis.variant_hash,
            'exonerated': exonerated,
            'strongly_exonerated': strongly_exonerated,
            'test_id': analysis.test_id,
        }
        luci_analysis_build_output.append(to_log)

      if test_names_to_exonerate:
        suite.add_known_luci_analysis_flaky_failures(test_names_to_exonerate)

    output_luci_analysis_step = self.m.step.empty('output LUCI Analysis')
    output_luci_analysis_step.presentation.properties['luci_analysis_info'] = (
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
    # For remote test suites, if we have valid test results from one of the runs
    # of a test suite, then that test suite by definition doesn't have invalid
    # test results. Local test suites don't get retried in 'retry shards with
    # patch' steps, so all invalid local suites are still invalid
    invalid_local_suites = [t for t in old_invalid_suites if t.runs_locally]
    still_invalid_remote_suites = list(
        set(old_invalid_suites).intersection(retried_invalid_suites))
    return still_invalid_remote_suites + invalid_local_suites

  def _should_abort_retry(self, failed_test_suites, suffix,
                          allowed_failing_suites):
    """Determines if the current recipe should skip its next retry phases.

    Args:
      failed_test_suites: A list of failed steps.Test suites to check
      suffix: The suffix for the context of the test run
      allowed_failing_suites: A set of suite names whose failures did not
        exceed the expectations and/or are suites names for experimental suites.
        Remove them from unexpected_failing_suites and do not retry them.
    Return:
      True if we should skip retries; False otherwise.
    """
    if self.m.tryserver.is_tryserver:
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
        s for s in failed_test_suites if s.failures_including_retry(suffix) and
        s.name not in allowed_failing_suites
    ]
    should_abort = len(unexpected) >= self._min_failed_suites_to_skip_retry
    total_test_cases_ran = 0
    total_test_cases_failed = 0
    for suite in unexpected:
      rdb_results = suite.get_rdb_results(suffix)
      total_test_cases_ran += rdb_results.total_tests_ran
      total_test_cases_failed += len(rdb_results.unexpected_failing_tests)
    if should_abort:
      result = self.m.step('abort retry', [])
      result.presentation.status = self.m.step.FAILURE
      result.presentation.step_text = (
          '\nskip retrying because there are >= {} test suites with test '
          'failures and it most likely indicates a problem with the CL. These '
          'suites being:\n{}'.format(self._min_failed_suites_to_skip_retry,
                                     '\n'.join(x.name for x in unexpected)))
    else:
      result = self.m.step('proceed with retry', [])
      result.presentation.step_text = (
          '\nfewer than {} failures, continue with retries'.format(
              self._min_failed_suites_to_skip_retry))

    # TODO(crbug.com/388310032): Consider incorporating these stats in the
    # "abort retry" logic above rather than using total suite numbers.
    result.presentation.properties['retry_decision_stats'] = {
        'total_test_cases_ran': total_test_cases_ran,
        'total_test_cases_failed': total_test_cases_failed,
    }

    return should_abort

  def _extract_retriable_suites(self, failed_suites, invalid_suites,
                                exonerated_suites_to_retry, retry_failed_shards,
                                retry_invalid_shards):
    target_suites = set(exonerated_suites_to_retry)
    if retry_failed_shards:
      target_suites.update(failed_suites)
    if retry_invalid_shards:
      target_suites.update(invalid_suites)
    # Only Swarming or Skylab suites can be usefully retried
    return [t for t in target_suites if t.runs_on_swarming or t.runs_on_skylab]

  def remove_retry_shards(self, suffix):
    """Helper to remove retry shards from the given suffix."""
    if not suffix.startswith(_RETRY_SUFFIX):
      return suffix
    if suffix == _RETRY_SUFFIX:
      return ''
    return suffix[len(_RETRY_SUFFIX) + 1:]

  def prepend_retry_shards(self, suffix):
    """Helper to prepend retry shards to the given suffix."""
    if not suffix:
      return _RETRY_SUFFIX
    return _RETRY_SUFFIX + ' ' + suffix

  def run_tests(self,
                checkout_dir: Path,
                source_dir: Path,
                build_dir: Path,
                test_suites,
                suffix,
                *,
                sort_by_shard=False,
                retry_failed_shards=False,
                retry_invalid_shards=False,
                include_utr_instruction=False):
    """Runs a list of test suites and returns the failed ones.

    If retry_[failed|invalid]_shards is true, this method retries shards that
    have deterministic failures or invalid results. Additionally, if the
    |_should_exonerate_flaky_failures| property is true, this method skips
    retrying deterministic failures that are already known to be flaky on ToT.

    Args:
      checkout_dir - The directory where the checkout was performed.
      source_dir - The path to the top-level repo.
      build_dir - Path to the build dir.
      test_suites - iterable of objects implementing the steps.Test interface.
      suffix - custom suffix, e.g. "with patch", "without patch" indicating
               context of the test run
      sort_by_shard - if True, trigger tests in descending order by number of
        shards required to run the test. Performance optimization.
      retry_failed_shards: If true, attempts to retry failed shards of swarming
                           tests, typically used with retry_invalid_shards.
      retry_invalid_shards: If true, attempts to retry shards of swarming tests
                            without valid results.
        triggering retry shards.
      include_utr_instruction: Whether or not to include UTR reproduction
                               instructions
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
    invalid_test_suites, failed_test_suites = (
        self.run_tests_once(
            checkout_dir,
            source_dir,
            build_dir,
            test_suites,
            suffix,
            sort_by_shard=sort_by_shard,
            include_utr_instruction=include_utr_instruction))

    exonerated_suites_to_retry = []
    if suffix == 'with patch':
      failed_test_suites, exonerated_suites_to_retry = (
          self._clean_failed_suite_list(failed_test_suites))

    # If we encounter any unexpected test results that we believe aren't due to
    # the CL under test, inform RDB of these tests so it keeps a record.
    self._exonerate_unrelated_failures(test_suites, suffix)

    experimental_suites = {x.name for x in test_suites if x.is_experimental}
    if retry_failed_shards and self._should_abort_retry(
        failed_test_suites, suffix, allowed_failing_suites=experimental_suites):
      return invalid_test_suites, invalid_test_suites + failed_test_suites

    failed_and_invalid_suites = list(
        set(failed_test_suites + invalid_test_suites))

    if not (retry_failed_shards or retry_invalid_shards):
      return invalid_test_suites, failed_and_invalid_suites

    swarming_test_suites = self._extract_retriable_suites(
        failed_test_suites, invalid_test_suites, exonerated_suites_to_retry,
        retry_failed_shards, retry_invalid_shards)
    if not swarming_test_suites:
      return invalid_test_suites, failed_and_invalid_suites

    output_retry_shard_step = self.m.step.empty('record ran_tests_retry_shard')
    output_retry_shard_step.presentation.properties[
        'ran_tests_retry_shard'] = True
    retry_suffix = self.prepend_retry_shards(suffix)
    new_swarming_invalid_suites, _ = self.run_tests_once(
        checkout_dir,
        source_dir,
        build_dir,
        swarming_test_suites,
        retry_suffix,
        sort_by_shard=True,
        include_utr_instruction=include_utr_instruction)

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
                           checkout_dir: Path,
                           source_dir: Path,
                           build_dir: Path,
                           test_suites,
                           retry_failed_shards=False,
                           include_utr_instruction=False):
    """Runs tests and returns failures.

    Args:
      checkout_dir: The directory where the checkout was performed.
      source_dir: The path to the top-level repo.
      build_dir: Path to the build dir.
      test_suites: iterable of objects implementing the steps.Test interface.
      retry_failed_shards: If true, attempts to retry failed shards of swarming
                           tests.
      include_utr_instruction: Whether or not to include UTR reproduction
                               instructions

    Returns: A tuple (invalid_test_suites, all_failing_test_suites).
      invalid_test_suites: Test suites that do not have valid test results.
      all_failing_test_suites:
          This includes test suites that ran but have failing tests, test suites
          that do not have valid test results, and test suites that failed with
          otherwise unspecified reasons. This is a superset of
          invalid_test_suites.
    """
    return self.run_tests(
        checkout_dir,
        source_dir,
        build_dir,
        test_suites,
        'with patch',
        sort_by_shard=True,
        retry_failed_shards=retry_failed_shards,
        retry_invalid_shards=retry_failed_shards,
        include_utr_instruction=include_utr_instruction,
    )

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
    valid, new_failures = test_suite.deterministic_without_patch_failures()
    if not valid:
      result = self.m.step.empty(
          '%s (test results summary)' % test_suite.name,
          step_text=('\n%s (without patch) did not produce valid results. '
                     'The suite likely reported no failed tests but exited '
                     'non-zero.') % test_suite.name)
      result.presentation.status = self.m.step.FAILURE
      self.m.tryserver.set_test_failure_tryjob_result()
      return False

    _, ignored_failures = test_suite.without_patch_failures_to_ignore()
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
    cmd += ['--bot-utils-gsutil-py-path', self.m.depot_tools.gsutil_py_path]
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

  def record_suite_statuses(self, test_suites, suffix):
    step_result = self.m.step.empty('record test suite statuses')
    step_result.presentation.properties['test_status'] = {}
    for test_suite in test_suites:
      if test_suite.is_enabled:
        step_result.presentation.properties['test_status'][
            test_suite.name] = test_suite.get_status(suffix)


class TestGroup:
  """Abstract class defines the shared interface for tests.

  Attributes:
    * test_suites - Iterable of objects implementing the steps.Test interface.
    * resultdb_api - Recipe API object for the resultdb recipe module.
  """

  def __init__(self, test_suites, resultdb_api=None):
    self._test_suites = test_suites
    self.resultdb_api = resultdb_api

  def pre_run(self,
              api,
              suffix,
              include_utr_instruction=False):  # pragma: no cover
    """Executes the |pre_run| method of each test.

    Args:
      api - The api object of this module.
      suffix - The test name suffix.
      include_utr_instruction: Whether or not to include UTR reproduction
                               instructions
    """
    raise NotImplementedError()

  def run(self, api, checkout_dir: Path, source_dir: Path, build_dir: Path,
          suffix):
    """Executes the |run| method of each test.

    Args:
      api - The api object of this module.
      checkout_dir - Path to directory where the checkout was performed.
      source_dir - Path to the top-level repo.
      build_dir - Path to the build dir.
      suffix - The test name suffix.
    """
    raise NotImplementedError()  # pragma: no cover

  @contextlib.contextmanager
  def _handle_test_errors(self, api):
    """Handle errors from running a function on a test."""
    try:
      yield
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


class LocalGroup(TestGroup):

  def __init__(self, test_suites, resultdb):
    super().__init__(test_suites, resultdb)

  def pre_run(self, api, suffix, include_utr_instruction=False):
    """Executes the |pre_run| method of each test."""
    for t in self._test_suites:
      with self._handle_test_errors(api):
        t.pre_run(suffix, include_utr_instruction)

  def run(self, api, checkout_dir: Path, source_dir: Path, build_dir: Path,
          suffix):
    """Executes the |run| method of each test."""
    for t in self._test_suites:
      with self._handle_test_errors(api):
        t.run(checkout_dir, source_dir, build_dir, suffix)
      api.test_utils.fetch_rdb_results(t, suffix)

    self.include_rdb_invocation(
        suffix, step_name='include local test invocations')


class SwarmingGroup(TestGroup):

  def __init__(self, test_suites, resultdb):
    super().__init__(test_suites, resultdb)
    self._task_ids_to_test = {}

  def pre_run(self, api, suffix, include_utr_instruction=False):
    """Executes the |pre_run| method of each test."""
    # Universal Test Runner is running on annotation run mode
    # which does not support concurrency
    if api.futures.concurrency_client.supports_concurrency:
      futures = []
      for t in self._test_suites:
        futures.append(
            api.futures.spawn_immediate(t.pre_run, suffix,
                                        include_utr_instruction))
      for f in futures:
        f.result()
    else:  #pragma nocover
      for t in self._test_suites:
        t.pre_run(suffix, include_utr_instruction)

    for t in self._test_suites:
      if not t.is_enabled:
        continue
      task = t.get_task(suffix)

      task_ids = tuple(task.get_task_ids())
      self._task_ids_to_test[task_ids] = t

    self.include_rdb_invocation(
        suffix, step_name='include swarming task invocations')

  def run(self, api, checkout_dir: Path, source_dir: Path, build_dir: Path,
          suffix):
    """Executes the |run| method of each test."""
    for test in self._test_suites:
      if not test.is_enabled:
        api.test_utils.fetch_rdb_results(test, suffix)
        test.run(checkout_dir, source_dir, build_dir, suffix)

    attempts = 0
    while self._task_ids_to_test:
      nest_name = 'collect tasks'
      if suffix:
        nest_name += ' (%s)' % suffix
      all_finished_sets = []
      with api.step.nest(nest_name):
        server_to_task_ids = collections.defaultdict(list)
        for task_ids, test in self._task_ids_to_test.items():
          server_to_task_ids[test.get_task(suffix).server].append(task_ids)
        # Dumbly invoke wait_for_finished_task_set() once for each server.
        # Ideally we'd invoke it just once and it'd interleave finished_sets
        # across multiple servers. As-is, it will likely return finished_sets
        # in an unbalanced order.
        for server, server_task_ids in server_to_task_ids.items():
          finished_sets, attempts = (
              api.chromium_swarming.wait_for_finished_task_set(
                  server_task_ids,
                  suffix=((' (%s)' % suffix) if suffix else ''),
                  attempts=attempts,
                  server=server))
          all_finished_sets.extend(finished_sets)
          for task_set in finished_sets:
            test = self._task_ids_to_test[tuple(task_set)]
            api.test_utils.fetch_rdb_results(test, suffix)

      for task_set in all_finished_sets:
        test = self._task_ids_to_test[tuple(task_set)]
        test.run(checkout_dir, source_dir, build_dir, suffix)
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
        api.test_utils.fetch_rdb_results(test, suffix)
        test.run(checkout_dir, source_dir, suffix)


class SkylabGroup(TestGroup):

  def __init__(self, test_suites, result_db):
    super().__init__(test_suites, result_db)
    self.ctp_build_timeout_sec = 3600
    # Overhead allowed for CTP in secs.
    self.ctp_overhead_allowed_time_sec = 600
    self.ctp_build_ids = set()

  def pre_run(self, api, suffix, include_utr_instruction=False):
    """Schedule each Skylab test request to a CTP build."""
    for t in self._test_suites:
      if not t.is_enabled:
        continue
      # Respect timeout of each test run by this CTP build.
      self.ctp_build_timeout_sec = max(t.spec.timeout_sec,
                                       self.ctp_build_timeout_sec)
      t.pre_run(suffix, include_utr_instruction)
      for build_id in t.ctp_build_ids.values():
        if not build_id in self.ctp_build_ids:
          self.ctp_build_ids.add(build_id)

    self.include_rdb_invocation(suffix, step_name='include skylab invocations')

  def run(self, api, checkout_dir: Path, source_dir: Path, build_dir: Path,
          suffix):
    """Render test results for each Skylab Test."""
    try:
      api.buildbucket.collect_builds(
          list(self.ctp_build_ids),
          # Collect CTP with slightly longer timeout.
          # CTP cancels child testers with ctp_build_timeout_sec, we should
          # allow some overhead for CTPs.
          timeout=self.ctp_build_timeout_sec +
          self.ctp_overhead_allowed_time_sec,
          step_name='collect skylab results')
    except api.step.StepFailure as err:
      # Perhaps some of the builds have completed, so continue
      # to collect their results even if the step timed out.
      if err.had_timeout:
        pass
      # For other types of errors returned from buildbucket, raise it to
      # draw some attentions.
      else:  # pragma: no cover
        raise

    for t in self._test_suites:
      # Skylab tests are executed by CrOS builders, which may retry upon
      # failures within their builds. So the same suffix may have multiple test
      # runs. We need to fetch all the results for a suffix, or the recipe
      # can not separate the flaky tests and deterministic failures.
      api.test_utils.fetch_rdb_results(t, suffix, force_fetch_all_results=True)
      with self._handle_test_errors(api):
        t.run(checkout_dir, source_dir, build_dir, suffix)
