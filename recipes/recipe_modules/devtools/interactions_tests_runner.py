# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from .test_runner_base import ExonerableTests, Results, GoldensCollector
from contextlib import contextmanager
import re


class InteractionsTests(ExonerableTests, GoldensCollector):

  @property
  def test_type_tag(self):
    return 'interactions_tests'

  def __init__(self,
               api,
               trigger,
               builder_config,
               step_name,
               bucket='devtools-frontend-screenshots'):
    self.bucket = bucket
    super().__init__(api, trigger, builder_config, False, step_name)

  def collect(self):
    if self.api.tryserver.is_tryserver:
      with self.api.step.nest(f'{self.step_name} shards results') \
        as presentation:
        self.api.chromium_swarming.collect_task(self.tasks[0])
      if presentation.status != self.api.step.SUCCESS:
        if presentation.status == self.api.step.EXCEPTION:
          return Results(infra_failures=[f'Infra Failure in {self.step_name}'])
        return Results(task_failures=[f'Failure in {self.step_name}'])
      return Results()

    return super().collect()

  def commands(self):
    return [self.run_tests_command('test/interactions')]

  @contextmanager
  def _collection_context(self):
    with self.api.devtools.collect_screenshots_on_trybot(self.bucket):
      yield

  def _post_collect(self):
    self.copy_golden_snapshots()

  def test_name_to_grep_string(self, name):
    name = re.sub(r'^interactions/[^:]*: ', '', name)
    return super().test_name_to_grep_string(name)
