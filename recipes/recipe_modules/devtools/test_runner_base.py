# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from .commons import Results

NODE_UNIT_TESTS_OPTION = '--node-unit-tests'

FLAKE_DETECTION_MAX_TESTS = 20
FLAKE_DETECTION_OPTION = '--repeat=10'
FLAKE_DETECTION_SKIPPED_TESTS_FOOTER = 'Skip-Flake-Detection'
FLAKE_DETECTION_SKIPPED_TESTS_PATTERN_FOOTER = 'Skip-Flake-Detection-Pattern'

class DevToolsTests(ABC):

  def __init__(self, api, trigger, builder_config, coverage, step_name):
    self.api = api
    self.sw_trigger = trigger
    self.builder_config = builder_config
    self.step_name = step_name
    self.output_dir = self.api.path.mkdtemp()
    self.tasks = []
    self.coverage = coverage
    self.env = {}
    self.extra_args = []
    self.is_flake_exoneration = False
    self.exoneration_tests = []
    # Used to accumulate results from the rerun and the original run
    self.results = Results()

  @property
  @abstractmethod
  def test_type_tag(self):
    """
    The tag that identifies the type of tests we are running. This is used to
    identify the tests that we want to rerun in case of flakiness.
    """

  def skip(self):
    return False

  def collect(self):
    """
    Returns a Results object that contains a list of the infra failures and
    another one for test failures (empty lists if there are no failures).
    """
    results = Results()
    with self.api.step.nest(f'{self.step_name} shards results'):
      for i in range(len(self.tasks)):
        step, is_valid = self.api.chromium_swarming.collect_task(self.tasks[i])
        if not is_valid:
          results.add_infra_failure(
              f'Infra Failure in {self.step_name} (shard #{i})')
        elif step.presentation.status != self.api.step.SUCCESS:
          results.add_test_failure(f'Failure in {self.step_name} (shard #{i})')

    return results

  def prepare_filtered_rerun(self, test_names):
    had_node_unit_tests = NODE_UNIT_TESTS_OPTION in self.extra_args
    # TODO: we probably should not override extra args.

    self.is_flake_exoneration = True

    self.extra_args = [
        '--retries=5',
    ]
    self.exoneration_tests = list(test_names)

    if had_node_unit_tests:
      self.extra_args.append(NODE_UNIT_TESTS_OPTION)
    # TODO (liviurau): add it back after puppeteer bug fix
    # https://github.com/puppeteer/puppeteer/pull/13901
    # self.env['DEBUG'] = 'puppeteer:*'

  def trigger(self, run_phase='default'):
    with self.api.step.nest(f'Trigger {self.step_name}'):
      self.tasks = self.sw_trigger.trigger(
          step_name=self.step_name,
          output_dir=self.output_dir,
          test_type_tag=self.test_type_tag,
          run_phase=run_phase,
          commands=self.commands(),
          env=self.construct_env(),
      )
    for task in self.tasks:
      # Remove 'invocations/' because it is added again in include_invocations.
      self.api.resultdb.include_invocations(
          [i[len('invocations/'):] for i in task.get_invocation_names()])

  def process_results(self):
    with self.api.step.nest(self.step_name):
      new_results = self.collect()
      if not new_results.infra_failures:
        try:
          self._post_collect()
        except self.api.step.StepFailure:
          new_results.add_infra_failure(
              f'Failed in post collect for {self.step_name}')
    if (self.is_flake_exoneration and self.results.exonerable() and
        new_results.can_exonerate()):
      new_results.exonerated_failures = self.results.task_failures
      self.results.task_failures = []
    self.results += new_results

  def _post_collect(self):
    """
    Hook for post collection steps.
    """

  def construct_env(self):
    return self.env

  def run_tests_command(self, test_list):
    command = [
        self.api.path.join('third_party', 'node', 'node.py'),
        '--output',
        f'out/{self.builder_config}/gen/test/run.js',
        '--artifacts-dir=${ISOLATED_OUTDIR}',
        '--skip-ninja',
        '--verbose=2',
        '--on-diff=throw',  # only used for screenshots tests; nop for others
    ]
    if self.coverage:
      command.append('--coverage')
    command.extend(self.extra_args)
    command.extend(test_list)
    return command

  @abstractmethod
  def commands(self):
    """
    Returns a list of commands to be run in the swarming tasks.
    """


class ExonerableTests(DevToolsTests):

  def __init__(self,
               api,
               trigger,
               builder_config,
               coverage,
               step_name,
               shard_count=1):
    super().__init__(api, trigger, builder_config, coverage, step_name)
    # Used to indicate that no task was triggered; may contain a failure if the
    # reason for not triggering qualifies as such
    self.skip_exoneration_result = None
    self.skip_deflaking_result = None
    self.owned_new_tests = []
    self.shard_count = shard_count
    self.shard_bias = 1

  @property
  @abstractmethod
  def test_patterns(self):
    pass

  def trigger_exoneration(self, test_names):
    """Triggers a rerun of specific tests identified as potentially flaky.

    This method is called during the exoneration phase to re-run tests that
    failed in the initial run. It filters the provided `test_names` to
    include only those relevant to the current test type, and then triggers
    a new swarming task to re-run them.

    Args:
      test_names: A dictionary where keys are test type tags (e.g.,
        'e2e_tests', 'unit_tests') and values are sets of test names
        (strings) that failed in the initial run.
    """
    owned_tests = test_names.get(self.test_type_tag)
    if not owned_tests:
      self.skip_exoneration_result = Results()
      return
    if len(owned_tests) > FLAKE_DETECTION_MAX_TESTS:
      self.skip_exoneration_result = Results()
      self.skip_exoneration_result.add_test_failure('Too many failures')
      self.api.step.empty(
          f'Too many tests to check for flakes {self.step_name}')
      return
    self.step_name += ' (rerun)'
    self.prepare_filtered_rerun(owned_tests)
    self.trigger('exoneration')

  def process_exoneration_results(self, test_names):
    """Processes the results of the exoneration rerun.

      This method is called after the exoneration rerun has completed. An
      implementation should handle cases where the rerun was skipped
      (due to no relevant tests or too many initial failures) by adding the skip
      result to the overall results. Otherwise, it processes the results of
      the rerun as a normal test run.

      Args:
        test_names (dict): A dictionary where:
          - keys are test type tags (e.g., 'e2e_tests', 'unit_tests').
          - values are sets of test names (strings) that were initially
            identified as failing.

      Returns:
        None
      """
    try:
      if self.skip_exoneration_result:
        self.results += self.skip_exoneration_result
        return
      self.process_results()
    finally:
      self.is_flake_exoneration = False

  @abstractmethod
  def owns_test(self, test: str) -> bool:
    pass

  def trigger_flake_detection(self, test_names):
    """Triggers a rerun of specific tests for flake detection.

      This method is called during the flake detection phase to re-run tests
      that have been recently added or modified. An implementation should filter
      the provided `test_names` to include only those relevant to the current
      test type, and then triggers a new swarming task to re-run them.

      Args:
        test_names (list): A list of test names (strings) that are candidates
          for flake detection.
      """
    self.is_flake_exoneration = False
    self.owned_new_tests = [test for test in test_names if self.owns_test(test)]
    if not self.owned_new_tests:
      self.skip_deflaking_result = Results()
      return

    self.shard_count = 1
    self.step_name += ' (flake detection)'
    if FLAKE_DETECTION_OPTION not in self.extra_args:
      self.extra_args.append(FLAKE_DETECTION_OPTION)
    self.trigger('flake detection')

  def process_flake_detection_results(self, test_names):
    """Processes the results of the flake detection rerun.

    This method is called after the flake detection rerun has completed.
    An implementation should handle cases where the rerun was skipped (due to
    no relevant tests) by adding the skip result to the overall results.
    Otherwise, it processes the results of the rerun as a normal test run.

    Args:
      test_names (list): A list of test names (strings) that were initially
        identified as candidates for flake detection.
    """
    if self.skip_deflaking_result:
      self.results += self.skip_deflaking_result
      return
    self.process_results()

  def sharding_args(self):
    return [[
        f'--shard-count={self.shard_count}', f'--shard-number={shard_number+1}',
        f'--shard-bias={self.shard_bias}'
    ] for shard_number in range(self.shard_count)]

  def commands(self):
    is_flake_detection_attempt = FLAKE_DETECTION_OPTION in self.extra_args
    if is_flake_detection_attempt:
      return [
          self.run_tests_command(args + self.owned_new_tests)
          for args in self.sharding_args()
      ]
    if self.is_flake_exoneration:
      return [self.run_tests_command(self.exoneration_tests)]
    return [
        self.run_tests_command(args + self.test_patterns)
        for args in self.sharding_args()
    ]
