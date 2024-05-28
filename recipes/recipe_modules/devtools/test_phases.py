# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from abc import ABC, abstractmethod
from collections import defaultdict
from contextlib import contextmanager

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
    inv_id = self.api.resultdb.current_invocation.replace('invocations/', '')
    unexpected_results = self.api.resultdb.query(
        inv_ids=[inv_id],
        variants_with_unexpected_results=True,
        tr_fields=['testId', 'tags'],
    )

    def tag_value(result, tag):
      return next(t for t in result.tags if t.key == tag).value

    for inv in unexpected_results.values():
      for result in inv.test_results:
        test_type = tag_value(result, 'test_type')
        self.test_names[test_type].add(result.test_id)

  def nesting_name(self):
    return 'Flake exonaration attempt'

  def trigger(self, runner):
    runner.trigger_exoneration(self.test_names)

  def process_results(self, runner):
    runner.process_exoneration_results(self.test_names)
