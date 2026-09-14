# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re
from typing import Protocol, Sequence

import attr

from recipe_engine import recipe_api
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build.chromium_tests import steps


class Test(Protocol):
  """A test case to rerun during bisection."""

  test_suite_name: str
  test_name: str
  test_id: str


class LuciBisectionApi(recipe_api.RecipeApi):
  def single_revision_steps(
    self,
    builder_id: chromium_types.BuilderId,
    builder_config: ctbc.BuilderConfig,
    compile_targets: Sequence[str] | None = None,
    analysis_id: int | None = None,
    bisection_host: str | None = None,
    should_clobber: bool = False,
  ) -> result_pb.RawResult:
    """Runs compile steps for a particular revision.

    Args:
      builder_id: The ID of the builder to compile for.
      builder_config: Configuration for the builder.
      compile_targets: Optional list of specific targets to compile. If omitted
        or empty, compiles all targets configured for the builder.
      analysis_id: Analysis ID for LUCI Bisection to update rerun progress.
      bisection_host: Hostname of the LUCI Bisection service.
      should_clobber: Whether to clean up the build directory before running
        hooks.

    Returns:
      RawResult with the compile step status and markdown summary.
    """
    compile_status = common_pb.INFRA_FAILURE
    try:
      self.m.chromium_tests.configure_build(builder_config)
      if should_clobber:
        self.m.chromium.c.clobber_before_runhooks = True

      # Check out the code.
      bot_update_step, build_dir, build_config = (
        self.m.chromium_tests.prepare_checkout(
          builder_config, set_output_commit=False
        )
      )
      self.m.chromium_swarming.configure_swarming(precommit=False)

      targets = (
        tuple(compile_targets)
        if compile_targets
        else build_config.compile_targets
      )

      try:
        compile_result, _ = self.m.chromium_tests.compile_specific_targets(
          build_dir,
          builder_id,
          builder_config,
          bot_update_step,
          build_config,
          targets,
          override_execution_mode=ctbc.COMPILE_AND_TEST,
          tests=[],
        )
        compile_status = compile_result.status
      except self.m.step.StepFailure:
        # Handle failures from generate_build_files or compile steps.
        # These should be treated as FAILURE, not INFRA_FAILURE.
        compile_status = common_pb.FAILURE
        raise
      markdown = (
        "LUCI Bisection's compile result from the input commit was "
        f"{self.rerun_result(compile_status)}."
      )
      return result_pb.RawResult(
        status=compile_status, summary_markdown=markdown
      )
    finally:
      self.send_result_to_luci_bisection(
        "send_result_to_luci_bisection",
        analysis_id,
        compile_status,
        bisection_host,
      )

  def test_single_revision_steps(
    self,
    builder_id: chromium_types.BuilderId,
    builder_config: ctbc.BuilderConfig,
    tests: Sequence[Test],
    bisection_host: str | None = None,
    should_clobber: bool = False,
    run_all: bool = False,
  ) -> result_pb.RawResult:
    """Runs tests for a particular revision.

    Args:
      builder_id: The ID of the builder to compile and test with.
      builder_config: Configuration for the builder.
      tests: List of tests to run, each specifying test suite and test name.
      bisection_host: Hostname of the LUCI Bisection service.
      should_clobber: Whether to clean up the build directory before running
        hooks.
      run_all: Whether to run all tests in each test suite rather than only the
        filtered tests in tests.

    Returns:
      RawResult with the build status.
    """
    test_results = []
    # Whether the build finishes running the tests and collecting results.
    run_succeeded = False

    try:
      self.m.chromium_swarming.add_default_tag('is_luci_bisection:true')
      self.m.chromium_tests.base_variant_getter = lambda spec: {
        'builder': spec.waterfall_buildername,
      }

      self.m.chromium_tests.configure_build(builder_config)
      if should_clobber:
        self.m.chromium.c.clobber_before_runhooks = True

      with self.m.chromium.chromium_layout():
        update_result, build_dir, targets_config = (
          self.m.chromium_tests.prepare_checkout(
            builder_config, set_output_commit=False
          )
        )
        self.m.chromium_swarming.configure_swarming(precommit=False)
        checkout_dir = update_result.checkout_dir
        source_dir = update_result.source_root.path

        step_tests, compile_targets = (
          self._compute_step_test_and_compile_targets(
            targets_config, tests, run_all
          )
        )
        self.m.step.empty(
          'Compute compile targets for test',
          step_text=(
            f"There are {len(compile_targets)} compile targets. "
            f"Compile targets are {compile_targets}."
          ),
        )

        # Compile.
        compile_result, compile_output = (
          self.m.chromium_tests.compile_specific_targets(
            build_dir,
            builder_id,
            builder_config,
            update_result,
            targets_config,
            compile_targets,
            override_execution_mode=ctbc.COMPILE_AND_TEST,
            tests=step_tests,
          )
        )

        if compile_result and compile_result.status != common_pb.SUCCESS:
          return result_pb.RawResult(status=compile_result.status)

        if compile_output:
          self.m.chromium_tests.isolate_test_targets(
            source_dir,
            build_dir,
            builder_config,
            update_result,
            compile_output,
          )

        # Run tests.
        # If we have < 10 tests to runs, trigger fast runs, which
        # gives the swarming task a higher priority.
        # Perhaps it worths it to check for only tests that actually exist,
        # but we are not doing it now, given that we rarely run more than
        # 10 tests, and most tests should exist anyway.
        if not run_all and len(tests) < 10:
          self.m.step.empty('adjust_fast_run_priority')
          self.m.chromium_swarming.default_priority -= 1

        suffix = 'bisection'
        with self.m.chromium_tests.wrap_chromium_tests(
          checkout_dir, source_dir, build_dir, tests=step_tests
        ):
          self.m.test_utils.run_tests_once(
            checkout_dir, source_dir, build_dir, step_tests, suffix
          )
        test_results = self._fetch_test_results(tests, step_tests, suffix)
        run_succeeded = True
    finally:
      self.send_test_results_to_luci_bisection(
        "send_test_results_to_luci_bisection",
        test_results,
        run_succeeded,
        bisection_host,
      )
    return result_pb.RawResult(status=common_pb.SUCCESS)

  def _fetch_test_results(self, tests: Sequence[Test], step_tests, suffix):
    test_ids_by_test_suite = {}
    for test in tests:
      test_ids = test_ids_by_test_suite.setdefault(test.test_suite_name, [])
      test_ids.append(test.test_id)
    test_results = []
    for test in step_tests:
      test_ids = test_ids_by_test_suite[test.canonical_name]
      res = self.m.resultdb.query_test_results(
        invocations=test.get_invocation_names(suffix),
        test_id_regexp="({})".format(
          "|".join([re.escape(id) for id in test_ids])
        ),
        field_mask_paths=['test_id', 'variant_hash', 'expected', 'status'],
        step_name=f'query_test_results {test.canonical_name}',
      )
      test_results.extend(res.test_results)
    return test_results

  def _compute_step_test_and_compile_targets(
    self, targets_config, tests: Sequence[Test], run_all: bool
  ):
    """Returns the step tests and compile targets.

    The step tests will be set with the test filter to run only the
    requested tests.
    """
    test_names_by_test_suite = {}
    for test in tests:
      test_names = test_names_by_test_suite.setdefault(test.test_suite_name, [])
      test_names.append(test.test_name)

    test_suites = []
    compile_targets = []
    for test in targets_config.all_tests:
      if test.canonical_name in test_names_by_test_suite:
        # Only runs tests presented in tests.
        test_options = steps.TestOptions.create(
          retry_limit=0, run_disabled=True
        )
        if not run_all:
          test_filter = test_names_by_test_suite[test.canonical_name]
          test_options = attr.evolve(test_options, test_filter=test_filter)
          # Customise the number of shards only when the number of tests to
          # run is less than 100. Otherwise, use the default number of shards.
          # This is to avoid creating too many shards.
          if len(test_filter) < 100:
            nshards = len(test_filter) // 10 + 1
            test.spec = attr.evolve(test.spec, shards=nshards)
        test.test_options = test_options

        resultdb = test.spec.resultdb
        resultdb = attr.evolve(
          resultdb, base_tags=(('is_luci_bisection', 'true'),)
        )
        # Do not run the result handler when running the test.
        # This may prevent errors in the result handlers, for example,
        # when we don't have the permission to upload the result.
        test.spec = attr.evolve(
          test.spec, results_handler_name=None, resultdb=resultdb
        )
        test_suites.append(test)
        compile_targets.extend(test.compile_targets())
    if not test_suites:
      # No tests found.
      self.m.step.empty(
        'Error: No test is found',
        status=self.m.step.FAILURE,
        step_text=f'No test is found {tests}',
      )
    return test_suites, compile_targets

  def send_result_to_luci_bisection(self, step_name, analysis_id, result, host):
    bbid = str(self.m.buildbucket.build.id)
    bot_id = self.m.swarming.bot_id
    # Leave the test data here for the purpose of led testing, because led build has no bbid
    # bbid = "8804856879691073905"
    # analysis_id = "5655880053817344"

    method = "luci.bisection.v1.BotUpdates.UpdateAnalysisProgress"
    request_input = {
      "analysisId": analysis_id,
      "bbid": bbid,
      "botId": bot_id,
      "gitilesCommit": {
        "host": self.m.buildbucket.gitiles_commit.host,
        "project": self.m.buildbucket.gitiles_commit.project,
        "id": self.m.buildbucket.gitiles_commit.id,
        "ref": self.m.buildbucket.gitiles_commit.ref,
      },
      "rerunResult": {"rerunStatus": self.rerun_result(result)},
    }
    self._call_prpc(step_name, host, method, request_input)

  def send_test_results_to_luci_bisection(
    self, step_name, test_results, run_succeeded, host
  ):
    bbid = str(self.m.buildbucket.build.id)
    bot_id = self.m.swarming.bot_id
    # Leave the test data here for the purpose of led testing, because led build has no bbid
    # bbid = "8804856879691073905"

    method = "luci.bisection.v1.BotUpdates.UpdateTestAnalysisProgress"
    request_input = {
      "bbid": bbid,
      "botId": bot_id,
      "results": self._rerun_test_results(test_results),
      "runSucceeded": run_succeeded,
    }
    self._call_prpc(step_name, host, method, request_input)

  def _call_prpc(self, step_name, host, method, request_input):
    args = [
      'prpc',
      'call',
      host,
      method,
    ]
    result = self.m.step(
      step_name,
      args,
      stdin=self.m.json.input(request_input),
      stdout=self.m.json.output(add_json_log=True),
    )
    result.presentation.logs['input'] = self.m.json.dumps(
      request_input, indent=2
    )
    return result.stdout

  def _rerun_test_results(self, test_results):
    res = []
    for r in test_results:
      res.append(
        {
          "testId": r.test_id,
          "variantHash": r.variant_hash,
          "isExpected": r.expected,
          "status": r.status,
        }
      )
    return res

  def rerun_result(self, result):
    if result == common_pb.SUCCESS:
      return "RERUN_STATUS_PASSED"
    if result == common_pb.FAILURE:
      return "RERUN_STATUS_FAILED"
    if result == common_pb.INFRA_FAILURE:
      return "RERUN_STATUS_INFRA_FAILED"
    return "RERUN_STATUS_UNSPECIFIED"
