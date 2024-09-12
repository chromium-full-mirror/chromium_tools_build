# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re

from abc import ABC, abstractmethod
from contextlib import contextmanager
from .commons import Results

FLAKE_DETECTION_MAX_TESTS = 20


class DevToolsTests(ABC):

  def __init__(self, api, source_dir, trigger, builder_config, coverage,
               step_name):
    self.api = api
    self.source_dir = source_dir
    self.sw_trigger = trigger
    self.builder_config = builder_config
    self.step_name = step_name
    self.output_dir = self.api.path.mkdtemp()
    self.tasks = []
    self.coverage = coverage
    self.env = {}
    self.extra_args = []
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
    grep_arg = 'mocha-grep' if use_legacy_test_runner(self.api) else 'grep'
    self.extra_args = [
        f'--{grep_arg}="{self.test_names_to_grep_string(test_names)}"',
    ]
    self.env['DEBUG'] = 'puppeteer:*'

  def test_names_to_grep_string(self, names):
    # Keep sorted for stable test expectations.
    return '|'.join(
        sorted(self.test_name_to_grep_string(name) for name in names))

  def test_name_to_grep_string(self, name):
    # Escape JS regexp characters except slashes.
    escaped = re.sub(r'([\-\\^$*+?.()|[\]{}])', r'\\\1', name)
    # We need to deal with slashes separately. Test IDs contain slashes that
    # are actual spaces in the test name, while some tests have slashes in
    # their name.
    return escaped.replace('/', '.')

  def trigger(self):
    with self.api.step.nest(f'Trigger {self.step_name}'):
      self.tasks = self.sw_trigger.trigger(
          step_name=self.step_name,
          output_dir=self.output_dir,
          test_type_tag=self.test_type_tag,
          commands=self.commands(),
          env=self.construct_env(),
      )
    for task in self.tasks:
      # Remove 'invocations/' because it is added again in include_invocations.
      self.api.resultdb.include_invocations(
          [i[len('invocations/'):] for i in task.get_invocation_names()])

  def process_results(self):
    with self.api.step.nest(self.step_name):
      with self._collection_context():
        new_results = self.collect()
        if not new_results.infra_failures:
          try:
            self._post_collect()
          except self.api.step.StepFailure:
            new_results.add_infra_failure(
                f'Failed in post collect for {self.step_name}')
    if self.results.exonerable() and new_results.can_exonerate():
      new_results.exonerated_failures = self.results.task_failures
      self.results.task_failures = []
    self.results += new_results

  @contextmanager
  def _collection_context(self):
    """
    Provides a context for the collection of the tasks.
    """
    yield

  def _post_collect(self):
    """
    Hook for post collection steps.
    """

  def trigger_exoneration(self, test_names):
    pass

  def process_exoneration_results(self, test_names):
    pass

  def construct_env(self):
    return self.env

  def run_tests_command(self, *test_list):
    command = [
        self.api.path.join('third_party', 'node', 'node.py'),
        '--output',
        f'out/{self.builder_config}/gen/test/run.js',
        '--artifacts-dir=${ISOLATED_OUTDIR}',
        '--skip-ninja',
        '--verbose=2',
    ]
    if self.coverage:
      command.append('--coverage')
    command.extend(self.extra_args)
    command.extend(test_list)
    return command

  def commands(self):
    if use_legacy_test_runner(self.api):
      return self.legacy_construct_commands()
    return self.construct_commands()

  @abstractmethod
  def construct_commands(self):
    """
    Returns a list of commands to be run in the swarming tasks.
    """

  @abstractmethod
  def legacy_construct_commands(self):
    """
    Returns a list of commands to be run in the swarming tasks.
    """


# TODO(liviurau) Remove this function after last legacy branch is no longer
# supported (https://chromium.googlesource.com/devtools/devtools-frontend/+/refs/heads/infra/config/definitions.star)
def use_legacy_test_runner(api):
  branch_number = api.properties.get('branch_number', None)
  last_branch_with_legacy_runner = 6478
  return branch_number and int(branch_number) <= last_branch_with_legacy_runner


class ExonerableTests(DevToolsTests):

  def __init__(self, api, source_dir, trigger, builder_config, coverage,
               step_name):
    super().__init__(api, source_dir, trigger, builder_config, coverage,
                     step_name)
    # Used to indicate that no task was triggered; may contain a failure if the
    # reason for not triggering qualifies as such
    self.skip_result = None

  def trigger_exoneration(self, test_names):
    owned_tests = test_names.get(self.test_type_tag)
    if not owned_tests:
      self.skip_result = Results()
      return
    if len(owned_tests) > FLAKE_DETECTION_MAX_TESTS:
      self.skip_result = Results()
      self.skip_result.add_test_failure('Too many failures')
      self.api.step.empty(
          f'Too many tests to check for flakes {self.step_name}')
      return
    self.step_name += ' (rerun)'
    self.prepare_filtered_rerun(owned_tests)
    self.trigger()

  def process_exoneration_results(self, test_names):
    if self.skip_result:
      self.results += self.skip_result
      return
    self.process_results()
