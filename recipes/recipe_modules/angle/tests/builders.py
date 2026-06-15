# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test to ensure the validity of the entries within BUILDERS.

Each entry in the BUILDERS dict will be checked to ensure
chromium_tests.configure_build can be called with a BuilderConfig for
that builder without error.

Copied from recipe_modules/chromium_tests/tests/builer.py.
"""

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.angle import builders as angle_builders
from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'angle',
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'recipe_engine/platform',
    'recipe_engine/properties',
]


def _normalize(x):
  if isinstance(x, (list, tuple)):
    return set(x)
  return x


def _validator(tester_value, builder_value, attrib):
  return (tester_value == builder_value,
          '%s mismatch between tester and builder' % attrib)


def validate_tester_config(api, builder_group, buildername, builder_config):
  parent_buildername = builder_config.parent_buildername

  parent_builder_group = builder_config.parent_builder_group or builder_group
  parent_builder_id = chromium_types.BuilderId.create_for_group(
      parent_builder_group, parent_buildername)
  parent_builder_spec = builder_config.builder_db[parent_builder_id]

  for a in ('chromium_config', 'chromium_apply_config',
            'chromium_config_kwargs', 'android_config'):
    tester_value = _normalize(getattr(builder_config, a))
    builder_value = _normalize(getattr(parent_builder_spec, a))

    valid, description = _validator(tester_value, builder_value, a)
    assert valid, description + (
        '\n  tester %s:%s: %s'
        '\n  builder %s:%s: %s') % (builder_group, buildername, tester_value,
                                    parent_builder_group, parent_buildername,
                                    builder_value)


def RunSteps(api):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder(
          builder_db=angle_builders.BUILDERS))

  # For testers, check that various configs are equal to the builder's
  if builder_config.execution_mode == ctbc.TEST:
    validate_tester_config(api, builder_id.group, builder_id.builder,
                           builder_config)

  # Make sure that the configuration is valid
  api.chromium_tests.configure_build(builder_config)


def GenTests(api):
  for builder_id, _ in sorted(angle_builders.BUILDERS.items()):
    builder_group = builder_id.group
    buildername = builder_id.builder
    yield api.test(
        ('%s-%s' % (builder_group, buildername)).replace(' ', '_'),
        api.properties(builder_group=builder_group, buildername=buildername),
        api.chromium_tests_builder_config.generic_build(
            builder_group=builder_group,
            builder=buildername,
            builder_db=angle_builders.BUILDERS),
        api.post_process(post_process.DropExpectation),
    )
