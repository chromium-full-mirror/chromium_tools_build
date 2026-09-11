# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test _get_arch_flag in code_coverage module."""

from __future__ import annotations

from dataclasses import dataclass

from recipe_engine import post_process
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import code_coverage
from RECIPE_MODULES.recipe_engine import assertions, platform, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  code_coverage: code_coverage.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  flag = api.code_coverage._get_arch_flag()
  expected = api.properties.get('expected_flag')
  api.assertions.assertEqual(list(flag), list(expected))


def GenTests(api: TEST_DEPS):
  yield api.test(
    'fallback_intel_64',
    api.platform('linux', 64, 'intel'),
    api.properties(expected_flag=['--arch', 'x86_64']),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'fallback_arm_64',
    api.platform('linux', 64, 'arm'),
    api.properties(expected_flag=['--arch', 'arm64']),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'fallback_arm_32',
    api.platform('linux', 32, 'arm'),
    api.properties(expected_flag=['--arch', 'armv7']),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'fallback_intel_32',
    api.platform('linux', 32, 'intel'),
    api.properties(expected_flag=['--arch', 'i386']),
    api.post_process(post_process.DropExpectation),
  )
