# Copyright 2020 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromiumdash


@dataclass
class DEPS(RecipeScriptApi):
  chromiumdash: chromiumdash.API


def RunSteps(api: DEPS):
  api.chromiumdash.releases('Android', 'Beta', 1)
  api.chromiumdash.milestones(3, only_branched=True)
  api.chromiumdash.milestones(0, only_active=True)
  api.chromiumdash.fetch_commit_info('abcdefg')


def GenTests(api: RecipeTestApi):
  yield api.test('basic')
