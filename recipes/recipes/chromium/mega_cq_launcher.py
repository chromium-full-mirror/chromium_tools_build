# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""A small orch-like recipe used to launch builds in Chromium's "Mega" CQ

This recipe can be removed in favor of native CV behavior once it reaches
parity with what this recipe provides. That is, only after at least these
bugs are fixed:
https://crbug.com/1483511
https://crbug.com/1483516
https://crbug.com/1484829
https://crbug.com/1487672
"""

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_mega_cq


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_mega_cq: chromium_mega_cq.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API


def RunSteps(api: DEPS):
  trybots = api.chromium_mega_cq.read_bots_file(
    'https://chromium.googlesource.com/chromium/src',
    'infra/config/generated/cq-usage/mega_cq_bots.txt',
  )

  api.chromium_mega_cq.sleep_until_off_peak()

  result, _ = api.chromium_mega_cq.trigger_and_collect_bots(trybots)
  return result


def GenTests(api: TEST_DEPS):

  yield api.test(
    'basic',
    api.chromium.try_build(),
    api.post_process(post_process.DropExpectation),
  )
