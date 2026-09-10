# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .test_runner_base import DevToolsTests


class LintCheck(DevToolsTests):
  def __init__(self, api, trigger, builder_config, step_name, target_os):
    super().__init__(api, trigger, builder_config, False, step_name)
    self.target_os = target_os.lower()

  @property
  def test_type_tag(self):
    return 'lint_check'

  def skip(self):
    is_debug_build = self.api.devtools.is_debug(self.builder_config)
    is_linux = self.target_os.startswith('ubuntu')
    return is_debug_build or not is_linux

  def commands(self):
    return [
      [
        self.api.path.join('third_party', 'node', 'node.py'),
        '--output',
        'scripts/test/run_lint_check.mjs',
      ]
    ]
