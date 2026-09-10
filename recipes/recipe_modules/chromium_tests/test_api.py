# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from google.protobuf import timestamp_pb2

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2

from recipe_engine import recipe_test_api


class ChromiumTestsApi(recipe_test_api.RecipeTestApi):
  @recipe_test_api.mod_test_data
  @staticmethod
  def change_size_limit(size_limit):
    """Returns an integer that limits test failure format size.

    This controls how many test failures are listed in the
    the string returned from _format_unrecoverable_failures()
    """
    return size_limit

  def read_targets_spec(
    self, builder_group, contents, step_prefix=None, step_suffix=None
  ):
    """Adds step data overrides for when a test reads a targets spec.

    Args:
      * builder_group: The group the test is using (ex. 'chromium.win').
      * contents: The contents of the source side spec file.
      * step_prefix: Any prefix to add to the step name. Useful if using step
        nesting.
      * step_suffix: Any suffix to append to the step name. Useful if the step
        runs twice.

    Returns:
      A recipe test object.
    """
    # Used to be called test specs, name has stuck around for now.
    filename = '%s.json' % builder_group

    return self.override_step_data(
      '%sread test spec (%s)%s'
      % (step_prefix or '', filename, step_suffix or ''),
      self.m.file.read_json(contents),
    )

  def gen_swarming_and_rdb_results(
    self,
    suite_name,
    suffix,
    extra_suffix=None,
    custom_os=None,
    invalid=False,
    failures=None,
    internal_failure=False,
    successes=None,
    flaky_failing_tests=None,
    expected_failures=None,
    skips=None,
    expected_skips=None,
  ):
    """Adds overrides for the swarming-collect and rdb-query steps of a test.

    To mock a swarming test's results, step data needs to be provided for the
    `swarming collect` command, as well as the `rdb query` command. This helper
    method can handle all that with less boilerplate.

    Note: when not overridden, a swarming test's results by default will be
    valid with zero failures. Consequently this method only needs to be invoked
    when simulating failures.

    Args:
      suite_name: Name of the suite.
      suffix: Phase of the build the test is in (eg "with patch").
      extra_suffix: Another suffix. Added specifically to GPU and android test
          step names in the recipe.
      custom_os: Custom swarming OS dimenion that gets embedded in the swarming
          test's step name.
      invalid: If True, marks the results as invalid.
      failures: List of names of test cases that failed.
      internal_failure: If True, marks the test's `has_incomplete_shards` as
          True.`
      flaky_failing_tests: List of names of test cases that failed and passed.
      expected_failures: List of names of test cases that failed expectedly.
      expected_failures: List of names of test cases that expectedly skipped.
      skips: List of names of test cases that were unexpectedly skipped.
    """
    failures = failures if failures else []
    skips = skips if skips else []
    swarming_step_name = suite_name
    if extra_suffix:
      swarming_step_name += ' ' + extra_suffix
    if suffix:
      swarming_step_name += ' (%s)' % suffix
    if custom_os:
      swarming_step_name += ' on ' + custom_os
    rdb_step_name = 'collect tasks'
    if suffix:
      rdb_step_name += ' (%s)' % suffix
    if extra_suffix:
      rdb_step_name += '.%s %s results' % (suite_name, extra_suffix)
    else:
      rdb_step_name += '.%s results' % suite_name
    return self.override_step_data(
      swarming_step_name,
      self.m.chromium_swarming.canned_summary_output(
        self.m.json.output({}),
        failure=bool(invalid or failures or skips or flaky_failing_tests),
        internal_failure=internal_failure,
      ),
    ) + self.override_step_data(
      rdb_step_name,
      stdout=self.m.raw_io.output_text(
        self.m.test_utils.rdb_results(
          suite_name,
          failing_tests=failures,
          passing_tests=successes,
          flaky_failing_tests=flaky_failing_tests,
          expected_failing_tests=expected_failures,
          skipped_tests=skips,
          expected_skipped_tests=expected_skips,
        )
      ),
    )

  def simulate_previous_build(self, test_statuses=None):
    """Simulates a previous build for find_suites_to_skip

    Args:
      test_statuses: A mapping of test names to their status. E.g.
          {'browser_tests': 'Success'}
    """
    reusable_build = build_pb2.Build(
      id=1234,
      status='SUCCESS',
      create_time=timestamp_pb2.Timestamp(seconds=1598338800),
      output=build_pb2.Build.Output(),
    )
    reusable_build.output.properties['test_status'] = (
      test_statuses if test_statuses else {}
    )

    return self.m.buildbucket.simulated_search_results(
      [reusable_build],
      step_name=(
        'check previous builds for skippable test suites.find '
        'equivalent patchset builds'
      ),
    )
