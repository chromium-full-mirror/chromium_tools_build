# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.code_coverage import constants

DEPS = [
    'chromium_checkout',
    'code_coverage',
    'recipe_engine/assertions',
    'recipe_engine/properties',
    'recipe_engine/path',
]

PROPERTIES = {'expected_binaries': Property(kind=list)}


def RunSteps(api, expected_binaries):
  api.code_coverage.build_dir = api.chromium_checkout.src_dir.joinpath(
      'out', 'Release')
  binaries = sorted(
      list(
          api.code_coverage.get_binaries(['whatever'],
                                         may_use_binaries_list_file=True)))
  api.assertions.assertCountEqual(binaries, expected_binaries)


def GenTests(api):

  yield api.test(
      'basic',
      api.path.exists(
          api.chromium_checkout.src_dir.joinpath(
              'out', 'Release',
              constants.BINARY_RELATIVE_PATHS_JSON_FILE_NAME)),
      api.properties(expected_binaries=[
          api.chromium_checkout.src_dir.joinpath('out', 'Release',
                                                 'some_library'),
      ]),
      api.post_process(post_process.DropExpectation),
  )
