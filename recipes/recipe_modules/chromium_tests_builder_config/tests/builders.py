# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test to ensure the validity of the entries within BUILDERS.

Each entry in the BUILDERS dict will be checked to ensure
chromium_tests.configure_build can be called with a BuilderConfig for
that builder without error.
"""

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.recipe_engine import platform, properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API


def _normalize(x):
  if isinstance(x, (list, tuple)):
    return set(x)
  return x


VALIDATORS = {
  (
    'chromium.memory',
    'Linux ASan Tests (sandboxed)',
    'chromium_apply_config',
  ): lambda tester_value, builder_value: (
    builder_value - tester_value == set(['lsan']),
    'chromium_apply_config for tester should be '
    "the same as its builder with 'lsan' removed",
  ),
}


def validate_tester_config(
  api: DEPS, builder_group, buildername, builder_config
):
  # Some builders are 'dummy' builders. They don't actually run, but are created
  # for configuration reasons. Don't validate these builders.
  if 'dummy' in buildername:
    return  # pragma: no cover
  parent_buildername = builder_config.parent_buildername

  parent_builder_group = builder_config.parent_builder_group or builder_group
  parent_builder_id = chromium_types.BuilderId.create_for_group(
    parent_builder_group, parent_buildername
  )
  parent_builder_spec = builder_config.builder_db[parent_builder_id]

  for a in (
    'chromium_config',
    'chromium_apply_config',
    'chromium_config_kwargs',
    'android_config',
  ):
    tester_value = _normalize(getattr(builder_config, a))
    builder_value = _normalize(getattr(parent_builder_spec, a))

    validator = VALIDATORS.get(
      (builder_group, buildername, a),
      lambda tester_value, builder_value: (
        tester_value == builder_value,
        '%s mismatch between tester and builder' % a,
      ),
    )

    valid, description = validator(tester_value, builder_value)
    assert valid, description + (
      '\n  tester %s:%s: %s\n  builder %s:%s: %s'
    ) % (
      builder_group,
      buildername,
      tester_value,
      parent_builder_group,
      parent_buildername,
      builder_value,
    )


def RunSteps(api: DEPS):
  builder_id, builder_config = (
    api.chromium_tests_builder_config.lookup_builder()
  )

  # For testers, check that various configs are equal to the builder's
  if builder_config.execution_mode == ctbc.TEST:
    validate_tester_config(
      api, builder_id.group, builder_id.builder, builder_config
    )

  # Make sure that the configuration is valid
  api.chromium_tests.configure_build(builder_config)


def GenTests(api: TEST_DEPS):
  for builder_id, _ in sorted(ctbc.builders.BUILDERS.items()):
    builder_group = builder_id.group
    buildername = builder_id.builder
    yield api.test(
      ('%s-%s' % (builder_group, buildername)).replace(' ', '_'),
      api.properties(builder_group=builder_group, buildername=buildername),
      api.chromium_tests_builder_config.generic_build(
        builder_group=builder_group, builder=buildername
      ),
      api.post_process(post_process.DropExpectation),
    )
