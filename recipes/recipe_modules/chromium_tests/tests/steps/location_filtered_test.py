# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests LocationFilteredTest when the affected files are not recorded."""

from __future__ import annotations

from dataclasses import dataclass

from recipe_engine import post_process
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_tests
from RECIPE_MODULES.recipe_engine import assertions

from RECIPE_MODULES.build.chromium_tests import steps


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API


def RunSteps(api: DEPS):
  spec = steps.LocationFilteredTestSpec.create(
    steps.MockTestSpec.create('inner_test'),
    location_filters=['third_party/chromium-bidi/.+'],
  )

  # Affected files are unknown (e.g. on the orchestrator): fail open and don't
  # cache the result.
  test = spec.get_test(api.chromium_tests)
  api.assertions.assertIsNone(
    api.chromium_tests.affected_files_for_location_filters
  )
  api.assertions.assertTrue(test.is_enabled)

  # Once the affected files are recorded, the filter is evaluated.
  api.chromium_tests.set_affected_files_for_location_filters(['foo/bar.cc'])
  api.assertions.assertFalse(test.is_enabled)


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.chromium.try_build(
      builder_group='fake-try-group', builder='fake-try-builder'
    ),
    api.post_process(post_process.DropExpectation),
  )
