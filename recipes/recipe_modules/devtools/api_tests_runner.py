# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .test_runner_base import ExonerableTests, GoldensCollector


class ApiTests(ExonerableTests, GoldensCollector):

  def __init__(self, api, trigger, builder_config, coverage, step_name):
    ExonerableTests.__init__(
        self, api, trigger, builder_config, coverage, step_name, shard_count=2)
    GoldensCollector.__init__(self, api, trigger, builder_config, coverage,
                              step_name)
    self.coverage = coverage
    self.shard_bias = 2

  @property
  def test_patterns(self):
    return [
        'front_end/**/*.test.api.ts',
        'mcp/**/*.test.api.ts',
        'inspector_overlay/**/*.test.api.ts',
    ]

  @property
  def test_type_tag(self):
    return 'api_tests'

  def owns_test(self, test: str) -> bool:
    folders = ('front_end/', 'mcp/', 'inspector_overlay/')
    return (any(test.startswith(folder) for folder in folders) and
            test.endswith('.test.api.ts'))

  def _post_collect(self):
    self.copy_golden_snapshots()
