# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DoesNotRun, MustRun, DropExpectation
from recipe_engine.recipe_api import Property
from recipe_engine.config import ConfigGroup, Dict, Single, List


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8, v8_auto_roller
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cipd,
  context,
  file,
  json,
  path,
  properties,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  cipd: cipd.API
  context: context.API
  file: file.API
  json: json.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  v8: v8.API
  v8_auto_roller: v8_auto_roller.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  file: file.TEST_API
  properties: properties.TEST_API


PROPERTIES = {
  'autoroller_config': Property(
    kind=ConfigGroup(
      excludes=Single(list, empty_val=None),
      includes=Single(list, empty_val=None),
    )
  ),
}


def RunSteps(api: DEPS, autoroller_config):
  # Add defaults
  autoroller_config = {
    'show_commit_log': False,
    'subject': 'Generic deps update',
    'manual_roll_reviewers': ['maik@example.com'],
    **autoroller_config,
  }

  update_result = api.v8_auto_roller.setup_target(
    'v8',
    'https://chromium.googlesource.com/v8/v8',
  )
  source_dir = update_result.source_root.path
  clm = api.v8_auto_roller.build_cl_manager(source_dir)
  api.v8_auto_roller.regular_roll(autoroller_config, clm, source_dir)

  return api.v8_auto_roller.report_result()


def GenTests(api: TEST_DEPS):
  def test(name, autoroller_config, chromium_deps, v8_deps, *expectations):
    return api.test(
      name,
      api.properties(autoroller_config=autoroller_config),
      api.override_step_data(
        'Find updated deps.Read v8/DEPS',
        api.file.read_text(v8_deps),
      ),
      api.override_step_data(
        'Find updated deps.Read src/DEPS',
        api.file.read_text(chromium_deps),
      ),
      *expectations,
      api.post_process(DropExpectation),
    )

  # includes / excludes
  ie_v8_deps = "deps = {'third_party/icu': 'https://chromium.googlesource.com/chromium/deps/icu.git@364118a1d9da24bb5b770ac3d762ac144d6da5a4'}"
  ie_chromium_deps = "deps = {'src/third_party/icu': 'https://chromium.googlesource.com/chromium/deps/icu.git@a622de35ac311c5ad390a7af80724634e5dc61ed'}"

  yield test(
    'includes_icu_valid',
    {'includes': ['third_party/icu']},
    ie_chromium_deps,
    ie_v8_deps,
    api.post_process(
      MustRun, 'Update trusted deps.gclient setdep third_party_icu'
    ),
  )

  yield test(
    'includes_invalid_dep',
    {'includes': ['v8/third_party/icu']},
    ie_chromium_deps,
    ie_v8_deps,
    api.expect_exception('AssertionError'),
  )

  yield test(
    'excludes_icu_valid',
    {'excludes': ['third_party/icu']},
    ie_chromium_deps,
    ie_v8_deps,
    api.post_process(
      DoesNotRun, 'Update trusted deps.gclient setdep third_party_icu'
    ),
  )

  yield test(
    'excludes_invalid_dep',
    {'excludes': ['v8/third_party/icu']},
    ie_chromium_deps,
    ie_v8_deps,
    api.expect_exception('AssertionError'),
  )
