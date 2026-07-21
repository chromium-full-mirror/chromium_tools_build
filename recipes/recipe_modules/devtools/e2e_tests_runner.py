# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .commons import Results
from .test_runner_base import (ExonerableTests, FLAKE_DETECTION_OPTION,
                               FLAKE_DETECTION_SKIPPED_TESTS_FOOTER)
from functools import cached_property
import re


class E2ENonHostedTests(ExonerableTests):

  def __init__(self, api, trigger, builder_config, step_name):
    super().__init__(
        api, trigger, builder_config, False, step_name, shard_count=4)
    self.skip_deflaking_result = None
    # TODO(liviurau): Needed only for where this cannot be passed in grep
    # pattern. To be removed once we have a ResultDB based solution.
    self.owned_new_tests = []

  @property
  def test_src_folders(self):
    return ['test/e2e_non_hosted']

  def skip(self):
    return self.api.devtools.is_debug(self.builder_config)

  def test_name_to_grep_string(self, name):
    name = re.sub(self.grep_filter_pattern, '', name)
    return super().test_name_to_grep_string(name)

  def trigger_exoneration(self, test_names):
    self.shard_count = 1
    return super().trigger_exoneration(test_names)

  @property
  def grep_filter_pattern(self):
    return r'^e2e(_non_hosted)?/[^:]*: '

  @property
  def test_type_tag(self):
    return 'e2e_non_hosted_tests'

  def trigger_flake_detection(self, test_names):
    skipped_tests = self.skipped_tests_for_flake_detection()
    self.owned_new_tests = [
        test for test in test_names
        if test.startswith('test/e2e') and test not in skipped_tests
    ]
    if not self.owned_new_tests:
      self.skip_deflaking_result = Results()
      return
    # TODO(liviurau): Should also take care of  large swiping changes that
    # touche a lot of tests, e.g. some refactoring. Add a maximum limit
    # TODO(liviurau): The divider needs rework.  E.g. the rerun count could be
    # used to divide. Instead of running 10 times on one shard you could run
    # the same 5 times on 2 shares, etc.
    self.shard_count = 1
    self.step_name += ' (flake detection)'
    # TODO(liviurau): There must be a better way to prepare a limited run.
    # Maybe pass the command function to the trigger function and have
    # discrete commands for normal and limited runs.
    self.extra_args = [FLAKE_DETECTION_OPTION]
    self.trigger('flake detection')

  def skipped_tests_for_flake_detection(self):
    return self.api.tryserver.get_footer(
        FLAKE_DETECTION_SKIPPED_TESTS_FOOTER
    ) if self.api.tryserver.is_tryserver else []

  def process_flake_detection_results(self, test_names):
    if self.skip_deflaking_result:
      self.results += self.skip_deflaking_result
      # Rerun was not triggered; nothing to preocess
      return
    self.process_results()


class RepeatE2EShuffledTests(E2ENonHostedTests):

  def trigger_exoneration(self, test_names):
    pass

  def process_exoneration_results(self, test_names):
    pass

  @property
  def test_type_tag(self):
    return 'shuffled_repeat_e2e_tests'
