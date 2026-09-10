# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from PB.recipe_modules.build.code_coverage.tests.api.get_binary_in_orchestrator_build import (
  InputProperties,
)

from RECIPE_MODULES.build.code_coverage import constants

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import code_coverage
from RECIPE_MODULES.recipe_engine import assertions, path, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  code_coverage: code_coverage.API
  path: path.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  path: path.TEST_API
  properties: properties.TEST_API


PROPERTIES = InputProperties


def RunSteps(api: DEPS, properties: InputProperties):
  api.code_coverage.build_dir = api.path.cleanup_dir
  binaries = sorted(
    list(
      api.code_coverage.get_binaries(
        ['whatever'], may_use_binaries_list_file=True
      )
    )
  )
  api.assertions.assertCountEqual(
    [str(b) for b in binaries], properties.expected_binaries
  )


def GenTests(api: TEST_DEPS):

  yield api.test(
    'basic',
    api.path.exists(
      api.path.cleanup_dir.joinpath(
        constants.BINARY_RELATIVE_PATHS_JSON_FILE_NAME
      )
    ),
    api.properties(
      expected_binaries=[
        '[CLEANUP]/some_library',
      ]
    ),
    api.post_process(post_process.DropExpectation),
  )
