# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .test_runner_base import ExonerableTests, NODE_UNIT_TESTS_OPTION


class UnitTests(ExonerableTests):

  def __init__(self,
               api,
               trigger,
               builder_config,
               coverage,
               step_name,
               node_unit_tests=False):
    super().__init__(
        api, trigger, builder_config, coverage, step_name, shard_count=2)
    self.coverage = coverage
    self.shard_bias = 2  # Use a bias to keep shards balanced
    self.node_unit_tests = node_unit_tests
    if node_unit_tests:
      self.shard_count = 1  # we have very few tests right now so 1 shard is sufficient.
      self.extra_args.append(NODE_UNIT_TESTS_OPTION)

  @property
  def test_patterns(self):
    return [
        'front_end/**/*.test.ts',
        'mcp/**/*.test.ts',
        'inspector_overlay/**/*.test.ts',
    ]

  @property
  def test_type_tag(self):
    return 'node_unit_tests' if self.node_unit_tests else 'unit_tests'

  def owns_test(self, test: str) -> bool:
    folders = ('front_end/', 'mcp/', 'inspector_overlay/')
    return (any(test.startswith(folder) for folder in folders) and
            test.endswith('.test.ts') and not test.endswith('.test.api.ts'))

  def _post_collect(self):
    if self.coverage and not self.is_flake_exoneration:
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
