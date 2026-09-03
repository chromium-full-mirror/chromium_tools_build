# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import buildbucket, cv


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  cv: cv.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API

from recipe_engine import post_process


def RunSteps(api: DEPS):
  api.tryserver.require_is_tryserver()

  api.buildbucket.hide_current_build_in_gerrit()
  api.cv.set_do_not_retry_build()
  api.cv.allow_reuse_for(api.cv.DRY_RUN, api.cv.FULL_RUN)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      api.post_process(post_process.DropExpectation))
