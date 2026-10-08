# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_bootstrap
from RECIPE_MODULES.recipe_engine import (
  assertions,
  buildbucket,
  json,
  properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_bootstrap: chromium_bootstrap.API
  json: json.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium_bootstrap: chromium_bootstrap.TEST_API
  json: json.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  # Initialize gets run when the recipe module is loaded
  pass


def GenTests(api: TEST_DEPS):

  yield api.test(
    'not-bootstrapped',
    api.post_check(post_process.DoesNotRun, 'bootstrapped properties'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'bootstrapped',
    api.chromium_bootstrap.properties(commits=[]),
    api.properties(foo='bar'),
    api.post_check(
      post_process.LogEquals,
      'bootstrapped properties',
      'properties',
      api.json.dumps(
        {
          "$build/chromium_bootstrap": {},
          "foo": "bar",
          "recipe": "chromium_bootstrap:tests/initialize",
        },
        indent=2,
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'bootstrapped-in-official-bucket',
    api.buildbucket.ci_build(bucket='official'),
    api.chromium_bootstrap.properties(commits=[]),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(
      post_process.SummaryMarkdown,
      "Uncaught exception: "
      "AssertionError('Official builders cannot be bootstrapped')",
    ),
    api.post_process(post_process.DropExpectation),
  )
