# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from abc import ABC, abstractmethod
from contextlib import contextmanager
from recipe_engine.recipe_api import InfraFailure, StepFailure

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

FLAKE_DETECTION_MAX_TESTS = 20


class DevToolsTests(ABC):

  def __init__(self, api, source_dir, cas_digest, builder_config, coverage,
               step_name):
    self.api = api
    self.source_dir = source_dir
    self.cas_digest = cas_digest
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

  def test_names_to_grep_string(self, names):
    return '|'.join([self.test_name_to_grep_string(name) for name in names])

  def test_name_to_grep_string(self, name):
    return name.replace('/', ' ')

  def trigger(self):
    with self.api.step.nest(f'Trigger {self.step_name}'):
      self.tasks = self.api.devtools.trigger_test_swarming_tasks(
          step_name=self.step_name,
          cas_digest=self.cas_digest,
          task_output_dir=self.output_dir,
          rdb_test_type=self.test_type_tag,
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

  def __init__(self, api, source_dir, cas_digest, builder_config, coverage,
               step_name):
    super().__init__(api, source_dir, cas_digest, builder_config, coverage,
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


class Results():

  def __init__(self,
               infra_failures=None,
               task_failures=None,
               exonerated_failures=None):
    self.infra_failures = infra_failures or []
    self.task_failures = task_failures or []
    self.exonerated_failures = exonerated_failures or []

  def __add__(self, result):
    return Results(
        self.infra_failures + result.infra_failures,
        self.task_failures + result.task_failures,
        self.exonerated_failures + result.exonerated_failures,
    )

  def exonerable(self):
    """True if the result contains only test failures.
    Infra failures are not exonerable."""
    return bool(self.task_failures and not self.infra_failures)

  def can_exonerate(self):
    """True if this result can exonerate other results."""
    return not bool(self.task_failures or self.infra_failures)

  def add_infra_failure(self, failure):
    self.infra_failures.append(failure)

  def add_test_failure(self, failure):
    self.task_failures.append(failure)

  def raise_on_failure(self):
    """
    Prioritize test failures in order to be able to close the tree even if we
    have infra failures.
    """
    if self.task_failures:
      raise StepFailure(', '.join(self.task_failures))

    if self.infra_failures:
      raise InfraFailure(', '.join(self.infra_failures))

  def raw_result(self):
    self.raise_on_failure()
    summary = None
    if self.exonerated_failures:
      summary = 'Flaky tests exonerated: ' + ', '.join(self.exonerated_failures)
    return result_pb2.RawResult(
        summary_markdown=summary,
        status=common_pb.SUCCESS,
    )
