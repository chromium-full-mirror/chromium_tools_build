# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""A small orch-like recipe used to launch builds in V8's "Mega" CQ

This recipe can be removed once Chromium's mega-cq launcher recipe is
migrated to CV.
"""

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_mega_cq
from RECIPE_MODULES.recipe_engine import buildbucket


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium_mega_cq: chromium_mega_cq.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API


def RunSteps(api: DEPS):
  # TODO(https://crbug.com/336441276): Read list of trybots from V8.
  trybots = [
    (
      api.buildbucket.build.builder.project,
      api.buildbucket.build.builder.bucket,
      'v8_linux_noi18n_rel',
    ),
  ]

  result, _ = api.chromium_mega_cq.trigger_and_collect_bots(trybots)
  return result


def GenTests(api: TEST_DEPS):

  yield api.test(
    'basic',
    api.buildbucket.try_build(),
    api.post_process(post_process.DropExpectation),
  )
