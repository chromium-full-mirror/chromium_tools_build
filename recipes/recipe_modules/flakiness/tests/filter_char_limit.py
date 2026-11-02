# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import flakiness
from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build.flakiness import utils
from RECIPE_MODULES.recipe_engine import assertions, platform


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  flakiness: flakiness.API
  platform: platform.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  platform: platform.TEST_API


def RunSteps(api: DEPS):
  host_is_win = api.platform.is_win
  host_limit = (
    steps.WINDOWS_FILTER_CHAR_LIMIT
    if host_is_win
    else steps.DEFAULT_FILTER_CHAR_LIMIT
  )

  class FakeTest:
    def __init__(self, runs_on_swarming, is_win=False):
      self.runs_on_swarming = runs_on_swarming
      self._is_win = is_win

    def dispatches_to_windows(self):
      return self._is_win

  # Swarming tests use the OS of the swarming bot, not the host OS.
  api.assertions.assertEqual(
    utils.get_filter_char_limit(FakeTest(True, is_win=True), host_is_win),
    steps.WINDOWS_FILTER_CHAR_LIMIT,
  )
  api.assertions.assertEqual(
    utils.get_filter_char_limit(FakeTest(True, is_win=False), host_is_win),
    steps.DEFAULT_FILTER_CHAR_LIMIT,
  )

  # Local tests run on the host bot, so they use the host OS.
  api.assertions.assertEqual(
    utils.get_filter_char_limit(FakeTest(False), host_is_win), host_limit
  )

  # Each name counts 2 more characters for the '::' delimiter.
  api.assertions.assertEqual(utils.get_filter_length([]), 0)
  api.assertions.assertEqual(utils.get_filter_length(['a', 'bc']), 7)


def GenTests(api: TEST_DEPS):
  yield api.test(
    'linux host',
    api.platform('linux', 64),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'windows host',
    api.platform('win', 64),
    api.post_process(post_process.DropExpectation),
  )
