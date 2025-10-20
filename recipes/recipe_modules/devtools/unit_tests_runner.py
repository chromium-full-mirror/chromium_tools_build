# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .test_runner_base import ExonerableTests, GoldensCollector


class UnitTests(ExonerableTests, GoldensCollector):

  def __init__(self, api, trigger, builder_config, coverage, step_name):
    ExonerableTests.__init__(
        self, api, trigger, builder_config, coverage, step_name, shard_count=2)
    GoldensCollector.__init__(self, api, trigger, builder_config, coverage,
                              step_name)
    self.coverage = coverage
    self.shard_bias = 2  # Use a bias to keep shards balanced

  @property
  def test_home_dir(self):
    return 'front_end'

  @property
  def test_type_tag(self):
    return 'unit_tests'

  def _post_collect(self):
    self.copy_golden_snapshots()
    if self.coverage:
      self.copy_coverage_data()

  def copy_coverage_data(self):
    shard_output_dir = self.tasks[0].get_task_shard_output_dirs()[0]
    coverage_data_dir = self.output_dir / shard_output_dir / 'karma-coverage'
    self.api.file.rmtree(
        'remove coverage files if they exist',
        self.api.path.join(self.api.devtools.source_dir, 'karma-coverage'))
    self.api.file.copytree(
        'copy unit tests coverage data', coverage_data_dir,
        self.api.path.join(self.api.devtools.source_dir, 'karma-coverage'))
