# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import profiles
from RECIPE_MODULES.recipe_engine import assertions, path


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  path: path.API
  profiles: profiles.API


def RunSteps(api: DEPS):
  api.profiles.source_dir = api.path.cleanup_dir
  assert api.profiles.llvm_profdata_exec == api.profiles.source_dir.joinpath(
      'third_party', 'llvm-build', 'Release+Asserts', 'bin', 'llvm-profdata')
  new_path = '/some/other/path/llvm-profdata'
  api.profiles.llvm_profdata_exec = new_path
  assert api.profiles.llvm_profdata_exec == new_path
  weights = {'weight': 2}
  api.profiles.merge_profdata(
      'some_artifact', '.*', sparse=True, weights=weights)


def GenTests(api: RecipeTestApi):

  yield api.test(
      'basic',
      api.post_process(post_process.MustRun,
                       'merge all profile files into a single .profdata'),
      api.post_process(post_process.DropExpectation),
  )
