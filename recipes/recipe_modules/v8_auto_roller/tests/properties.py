# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DoesNotRun, MustRun, DropExpectation)
from recipe_engine.recipe_api import Property
from recipe_engine.config import ConfigGroup, Dict, Single, List


DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'v8',
    'v8_auto_roller',
]


PROPERTIES = {
    'autoroller_config':
        Property(
            kind=ConfigGroup(
                excludes=Single(list, empty_val=None),
                includes=Single(list, empty_val=None),
            )),
}


def RunSteps(api, autoroller_config):
  # Add defaults
  autoroller_config = {
      'show_commit_log': False,
      'subject': 'Generic deps update',
      'reviewers': ['maik@example.com'],
      **autoroller_config
  }

  api.v8_auto_roller.setup_target(
      'v8',
      'https://chromium.googlesource.com/v8/v8',
  )
  clm = api.v8_auto_roller.build_cl_manager()
  api.v8_auto_roller.regular_roll(autoroller_config, clm)

  return api.v8_auto_roller.report_result()



def GenTests(api):
  icu_v8_deps = "v8/third_party/icu: https://chromium.googlesource.com/chromium/deps/icu.git@364118a1d9da24bb5b770ac3d762ac144d6da5a4"
  icu_chromium_deps = "src/third_party/icu: https://chromium.googlesource.com/chromium/deps/icu.git@a622de35ac311c5ad390a7af80724634e5dc61ed"

  yield api.test(
      'includes_icu_valid',

      api.properties(autoroller_config={
          'includes': ['third_party/icu'],
      }),

      api.override_step_data(
        'Find updated deps.gclient get v8 deps',
        api.raw_io.stream_output_text(icu_v8_deps, stream='stdout'),
      ),
      api.override_step_data(
        'Find updated deps.gclient get src deps',
        api.raw_io.stream_output_text(icu_chromium_deps, stream='stdout'),
      ),

      api.post_process(MustRun, 'Update trusted deps.gclient setdep third_party_icu'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'includes_invalid_dep',

      api.properties(autoroller_config={
          'includes': ['v8/third_party/icu'],
      }),

      api.override_step_data(
        'Find updated deps.gclient get v8 deps',
        api.raw_io.stream_output_text(icu_v8_deps, stream='stdout'),
      ),
      api.override_step_data(
        'Find updated deps.gclient get src deps',
        api.raw_io.stream_output_text(icu_chromium_deps, stream='stdout'),
      ),

      api.expect_exception('AssertionError'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'excludes_icu_valid',

      api.properties(autoroller_config={
          'excludes': ['third_party/icu'],
      }),

      api.override_step_data(
        'Find updated deps.gclient get v8 deps',
        api.raw_io.stream_output_text(icu_v8_deps, stream='stdout'),
      ),
      api.override_step_data(
        'Find updated deps.gclient get src deps',
        api.raw_io.stream_output_text(icu_chromium_deps, stream='stdout'),
      ),

      api.post_process(DoesNotRun, 'Update trusted deps.gclient setdep third_party_icu'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'excludes_invalid_dep',

      api.properties(autoroller_config={
          'excludes': ['v8/third_party/icu'],
      }),

      api.override_step_data(
        'Find updated deps.gclient get v8 deps',
        api.raw_io.stream_output_text(icu_v8_deps, stream='stdout'),
      ),
      api.override_step_data(
        'Find updated deps.gclient get src deps',
        api.raw_io.stream_output_text(icu_chromium_deps, stream='stdout'),
      ),

      api.expect_exception('AssertionError'),
      api.post_process(DropExpectation),
  )
