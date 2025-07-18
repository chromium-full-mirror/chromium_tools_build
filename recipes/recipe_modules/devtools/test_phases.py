# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from abc import ABC, abstractmethod
from collections import defaultdict
from .commons import Results

class TestRunPhase(ABC):

  def __init__(self, api):
    self.api = api

  def run_all(self, runners, task_on_builder=None):
    """ Run all runners in the phase on swarming.
    After triggering all runners, task_on_builder is called if provided to take
    advantage of the builder's idle resources.
    """
    with self.api.step.nest(self.nesting_name()):
      self.init_phase()
      with self.api.step.nest('Trigger Tests'):
        for r in runners:
          self.trigger(r)
      if task_on_builder:
        task_on_builder()
      for r in runners:
        self.process_results(r)
    return sum([t.results for t in runners], Results())

  @abstractmethod
  def nesting_name(self):
    pass  # pragma: no cover

  @abstractmethod
  def trigger(self, runner):
    pass  # pragma: no cover

  @abstractmethod
  def process_results(self, runner):
    pass  # pragma: no cover

  def init_phase(self):
    pass


class FirstRunPhase(TestRunPhase):

  def nesting_name(self):
    return 'Run tests'

  def trigger(self, runner):
    runner.trigger()

  def process_results(self, runner):
    runner.process_results()


class ExonerationPhase(TestRunPhase):

  def __init__(self, api):
    super().__init__(api)
    self.test_names = defaultdict(set)

  def init_phase(self):
    for test_id, test_type in self.unexpected_results():
      self.test_names[test_type].add(test_id)

  def unexpected_results(self):
    proto_results = self.get_proto_results()
    unique_bare_results = self.get_unique_result_values(proto_results)
    return self.get_tests_with_only_failures(unique_bare_results)

  def get_proto_results(self):
    inv_id = self.api.resultdb.current_invocation.replace('invocations/', '')
    response = self.api.resultdb.query(
        inv_ids=[inv_id],
        tr_fields=['testId', 'tags', 'expected'],
        limit=0,
    )
    return sum((res.test_results for res in response.values()), [])

  def get_unique_result_values(self, proto_results):
    return set((r.test_id, get_test_type(r), r.expected) for r in proto_results)

  def get_tests_with_only_failures(self, unique_bare_results):
    passing_tests = set()
    failing_tests = set()
    for (test_id, test_type, expected) in unique_bare_results:
      (passing_tests if expected else failing_tests).add((test_id, test_type))
    tests_with_only_failures = list(failing_tests - passing_tests)
    return tests_with_only_failures

  def nesting_name(self):
    return 'Flake exonaration attempt'

  def trigger(self, runner):
    runner.trigger_exoneration(self.test_names)

  def process_results(self, runner):
    runner.process_exoneration_results(self.test_names)

  def run_all(self, runners, task_on_builder=None):
    results = super().run_all(runners, task_on_builder)
    if self.unexpected_results():
      results.add_test_failure('Failed to exonerate some of the failing tests')
    return results


def get_test_type(result):
  return next(t for t in result.tags if t.key == 'test_type').value


class FlakeDetectionPhase(TestRunPhase):

  def __init__(self, api):
    super().__init__(api)
    self.test_files = []

  def init_phase(self):
    with self.api.step.nest("find new tests") as presentation:
      with self.api.context(cwd=self.api.devtools.source_dir):
        self.test_files = self._find_touched_tests()
        presentation.logs['tests'] = self.test_files

  def nesting_name(self):
    return 'Detect flakes in new tests'

  def trigger(self, runner):
    runner.trigger_flake_detection(self.test_files)

  def process_results(self, runner):
    runner.process_flake_detection_results(self.test_files)

  def _find_touched_tests(self):
    """ Diff should not find anything since the current patch is applied on ToT,
    and normally show is what reveals the current changes. In some cases though
    (b/431698126), diff will find the changes, probably because in those
    situations the current patch is not applied correctly on bot checkouts.
    """
    git_changes = self._query_git('diff') or self._query_git('show')
    return [file for file in git_changes if file.endswith('test.ts')]

  def _query_git(self, command):
    """ Find files that were changed in the current patch."""
    filter_deleted = '--diff-filter=d'
    return self.api.v8.git_output(command, '--name-only', '--format=',
                                  filter_deleted).splitlines()
