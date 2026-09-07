# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import isolate
from RECIPE_MODULES.recipe_engine import file, path


@dataclass
class DEPS(RecipeScriptApi):
  file: file.API
  isolate: isolate.API
  path: path.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  file: file.TEST_API


def RunSteps(api: DEPS):
  source_dir = api.path.cache_dir / 'builder' / 'src'
  browser_test_path = source_dir / 'out/Release/browser_tests'

  api.isolate.add_files_to_isolate_file(source_dir / 'out/Release/test.isolate',
                                        [browser_test_path])


def GenTests(api: TEST_DEPS):
  yield api.test(
      'add_files',
      api.override_step_data(
          'Read [CACHE]/builder/src/out/Release/test.isolate',
          api.file.read_json({'cmd': ''})),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'not_found',
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
