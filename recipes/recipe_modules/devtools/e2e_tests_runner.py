# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .commons import Results
from .test_runner_base import (ExonerableTests, FLAKE_DETECTION_OPTION,
                               FLAKE_DETECTION_SKIPPED_TESTS_FOOTER)
from functools import cached_property


class E2ETests(ExonerableTests):

  def __init__(self, api, trigger, builder_config, step_name):
    super().__init__(
        api, trigger, builder_config, False, step_name, shard_count=4)

  @property
  def test_patterns(self):
    return ['test/e2e']

  def owns_test(self, test):
    return test.startswith('test/e2e')

  def skip(self):
    return self.api.devtools.is_debug(self.builder_config)

  def trigger_exoneration(self, test_names):
    self.shard_count = 1
    return super().trigger_exoneration(test_names)

  @property
  def test_type_tag(self):
    return 'e2e_tests'


class RepeatE2EShuffledTests(E2ETests):

  def trigger_exoneration(self, test_names):
    pass

  def process_exoneration_results(self, test_names, coordinator=None):
    pass

  @property
  def test_type_tag(self):
    return 'shuffled_repeat_e2e_tests'
