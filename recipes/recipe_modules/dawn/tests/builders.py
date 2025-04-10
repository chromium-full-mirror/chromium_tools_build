# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests to ensure the validity of the entries within builders.py.

Each entry will be checked to ensure dawn.ci_steps can be called with a
BuilderConfig for that builder without error.
"""

from recipe_engine import post_process

from RECIPE_MODULES.build.dawn import builders as dawn_builders
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_tests_builder_config',
    'dawn',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def _normalize_order(x):
  if isinstance(x, (list, tuple)):
    return set(x)
  return x


def validate_tester_config(api, builder_id, builder_config):
  tester_buildername = builder_id.builder
  tester_builder_group = builder_id.group

  parent_buildername = builder_config.parent_buildername
  parent_builder_group = builder_config.parent_builder_group or builder_id.group
  parent_builder_id = chromium.BuilderId.create_for_group(
      parent_builder_group, parent_buildername)
  parent_builder_config = builder_config.builder_db[parent_builder_id]

  tested_attributes = (
      'android_config',
      'chromium_apply_config',
      'chromium_config',
      'chromium_config_kwargs',
      'gclient_config',
      'simulation_platform',
  )
  for attr in tested_attributes:
    tester_value = _normalize_order(getattr(builder_config, attr))
    parent_value = _normalize_order(getattr(parent_builder_config, attr))

    if tester_value == parent_value:
      continue

    raise RuntimeError(  # pragma: no cover
        'Parent builder {parent_builder_group}:{parent_buildername} and child '
        'tester {tester_builder_group}:{tester_buildername} have mismatched '
        'values for attribute {attr}. Parent: {parent_value}, Child: '
        '{tester_value}'.format(
            parent_builder_group=parent_builder_group,
            parent_buildername=parent_buildername,
            tester_builder_group=tester_builder_group,
            tester_buildername=tester_buildername,
            attr=attr,
            parent_value=parent_value,
            tester_value=tester_value))


def RunSteps(api):
  builder_id, builder_config = api.chromium_tests_builder_config.lookup_builder(
      builder_db=dawn_builders.BUILDERS)

  # For testers, check that various configs are equal to the parent builder's.
  if builder_config.execution_mode == ctbc.TEST:
    validate_tester_config(api, builder_id, builder_config)

  api.dawn.ci_steps()
  api.step('Success', ['echo', 'Success!'])


def GenTests(api):
  for builder_id in sorted(dawn_builders.BUILDERS):
    builder_group = builder_id.group
    buildername = builder_id.builder
    yield api.test(
        ('%s-%s' % (builder_group, buildername)).replace(' ', '_'),
        api.chromium.ci_build(
            builder_group=builder_group,
            builder=buildername,
            project='dawn',
            git_repo='https://dawn.googlesource.com/dawn/'),
        api.post_process(post_process.DropExpectation),
    )
