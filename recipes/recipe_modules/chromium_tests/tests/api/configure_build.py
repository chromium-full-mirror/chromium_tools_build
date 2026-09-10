# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.config import Dict
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.recipe_engine import assertions, platform, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  gclient: gclient.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


PROPERTIES = {
  'test_only': Property(kind=bool, default=None),
  'expected_gclient_vars': Property(kind=Dict(), default={}),
}


def RunSteps(api: DEPS, test_only, expected_gclient_vars):
  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  kwargs = {}
  if test_only is not None:
    kwargs['test_only'] = test_only
  api.chromium_tests.configure_build(builder_config, **kwargs)
  for k, v in expected_gclient_vars.items():
    api.assertions.assertEqual(v, api.gclient.c.solutions[0].custom_vars.get(k))


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
    'android_apply_config',
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              android_config='base_config',
              chromium_config='main_builder',
              chromium_apply_config=['mb'],
              chromium_config_kwargs={
                'BUILD_CONFIG': 'Debug',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 32,
                'TARGET_PLATFORM': 'android',
              },
              gclient_config='chromium',
              android_apply_config=['use_devil_provision'],
            ),
          },
        }
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'target-cros-boards',
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              chromium_config_kwargs={
                'TARGET_PLATFORM': 'chromeos',
                'TARGET_CROS_BOARDS': 'fake-board',
              },
            ),
          },
        }
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'cros-boards-with-vm-optimized-qemu-images',
    api.properties(
      expected_gclient_vars={
        'cros_boards_with_qemu_images': 'amd64-generic-vm'
      },
    ),
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              chromium_config_kwargs={
                'TARGET_PLATFORM': 'chromeos',
                'CROS_BOARDS_WITH_QEMU_IMAGES': 'amd64-generic-vm',
              },
            ),
          },
        }
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'target-platform-incompatible-with-host-platform',
    api.platform('linux', 64),
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-tester',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-tester',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
          chromium_config_kwargs={
            'TARGET_PLATFORM': 'ios',
          },
        ),
      ).assemble()
    ),
    api.expect_exception('BadConf'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'tester-target-platform-incompatible-with-host-platform',
    api.platform('linux', 64),
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-tester',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_tester(
        builder_group='fake-group',
        builder='fake-tester',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
          chromium_config_kwargs={
            'TARGET_PLATFORM': 'mac',
          },
        ),
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'test-only-override',
    api.platform('linux', 64),
    api.chromium.generic_build(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
          chromium_config_kwargs={
            'TARGET_PLATFORM': 'mac',
          },
        ),
      ).assemble()
    ),
    api.properties(test_only=True),
    api.post_process(post_process.DropExpectation),
  )
