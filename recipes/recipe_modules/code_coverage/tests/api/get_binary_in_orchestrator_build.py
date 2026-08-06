# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from PB.recipe_modules.build.code_coverage.tests.api.get_binary_in_orchestrator_build import (
    InputProperties,)

from RECIPE_MODULES.build.code_coverage import constants

DEPS = [
    'code_coverage',
    'recipe_engine/assertions',
    'recipe_engine/properties',
    'recipe_engine/path',
]

PROPERTIES = InputProperties


def RunSteps(api, properties: InputProperties):
  api.code_coverage.build_dir = api.path.cleanup_dir
  binaries = sorted(
      list(
          api.code_coverage.get_binaries(['whatever'],
                                         may_use_binaries_list_file=True)))
  api.assertions.assertCountEqual([str(b) for b in binaries],
                                  properties.expected_binaries)


def GenTests(api):

  yield api.test(
      'basic',
      api.path.exists(
          api.path.cleanup_dir.joinpath(
              constants.BINARY_RELATIVE_PATHS_JSON_FILE_NAME)),
      api.properties(expected_binaries=[
          '[CLEANUP]/some_library',
      ]),
      api.post_process(post_process.DropExpectation),
  )
