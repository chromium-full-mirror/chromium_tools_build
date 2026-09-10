# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .test_runner_base import ExonerableTests


class ApiTests(ExonerableTests):
  def __init__(self, api, trigger, builder_config, step_name):
    super().__init__(
      api, trigger, builder_config, False, step_name, shard_count=1
    )
    self.shard_bias = 1

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
    return any(test.startswith(folder) for folder in folders) and test.endswith(
      '.test.api.ts'
    )
