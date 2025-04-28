# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from .test_runner_base import ExonerableTests


class UnitTests(ExonerableTests):

  @property
  def test_type_tag(self):
    return 'unit_tests'

  def commands(self):
    return [self.run_tests_command('front_end')]

  def _post_collect(self):
    if self.coverage:
      self.copy_coverage_data()

  def copy_coverage_data(self):
    shard_output_dir = self.tasks[0].get_task_shard_output_dirs()[0]
    coverage_data_dir = self.output_dir / shard_output_dir / 'karma-coverage'
    self.api.file.rmtree('remove coverage files if they exist',
                         self.api.path.join(self.source_dir, 'karma-coverage'))
    self.api.file.copytree(
        'copy unit tests coverage data', coverage_data_dir,
        self.api.path.join(self.source_dir, 'karma-coverage'))
