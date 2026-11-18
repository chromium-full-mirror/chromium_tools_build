# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re

from .test_runner_base import DevToolsTests

BASE_SUITES = [
  'scripts/eslint_rules/tests/',
  'scripts/stylelint_rules/tests/',
  'scripts/build/tests/',
]

# Suites wired into devtools_script_tests after chromium/8158 branched
# (https://crrev.com/c/8776219 and https://crrev.com/c/8797346; chromium/8159
# is the first release branch with both CLs).
AI_SCRIPTS_SUITES = [
  'scripts/ai_assistance/',
  'test/ai_evals/report/',
]

LAST_PRE_AI_SCRIPTS_BRANCH = 8158
_RELEASE_BRANCH_RE = re.compile(
  r'^refs/(?:heads/chromium|branch-heads)/([0-9]+)(?:_[0-9]+)*$'
)


class ScriptsTests(DevToolsTests):
  def __init__(self, api, trigger, builder_config, step_name, target_os):
    super().__init__(api, trigger, builder_config, False, step_name)
    self.target_os = target_os.lower()

  @property
  def test_type_tag(self):
    return 'scripts_tests'

  def skip(self):
    is_debug_build = self.api.devtools.is_debug(self.builder_config)
    is_linux = self.target_os.startswith('ubuntu')
    return is_debug_build or not is_linux

  def _suites(self):
    target_ref = (
      self.api.tryserver.gerrit_change_target_ref
      or self.api.buildbucket.gitiles_commit.ref
    )
    match = _RELEASE_BRANCH_RE.match(target_ref or '')
    if match and int(match.group(1)) <= LAST_PRE_AI_SCRIPTS_BRANCH:
      return BASE_SUITES
    return BASE_SUITES + AI_SCRIPTS_SUITES

  def commands(self):
    base_command = [
      self.api.path.join('third_party', 'node', 'node.py'),
      '--output',
      'scripts/run_on_target.mjs',
      f'--target={self.builder_config}',
      'gen/test/run.js',
      '--skip-ninja',
    ]
    return [base_command + self._suites()]
