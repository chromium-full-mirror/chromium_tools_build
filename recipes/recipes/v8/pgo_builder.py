# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.recipe_api import Property

from recipe_engine.post_process import (
    DropExpectation,
    LogContains,
    LogDoesNotContain,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_builtins_pgo
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    properties,
    raw_io,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  v8_builtins_pgo: v8_builtins_pgo.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API

PROPERTIES = {
    'compilators': Property(kind=list, default=None),
    'swarming_service_account': Property(kind=str, default=None),
}


def RunSteps(api: DEPS, compilators, swarming_service_account):
  return api.v8_builtins_pgo.run(
      compilators=compilators,
      swarming_service_account=swarming_service_account)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'default',
      api.buildbucket.ci_build(bucket='ci-hp', revision=None),
      api.properties(compilators=[]),
      api.override_step_data(
          'init trackers for candidate versions.git ls-remote',
          api.raw_io.stream_output_text(
              '3456 refs/tags/12.2.1\n', stream='stdout')),
      api.post_process(LogContains,
                       'init trackers for candidate versions.gsutil cat',
                       'filtered tags', ['12.2.1.0 3456']),
      api.post_process(DropExpectation),
  )
