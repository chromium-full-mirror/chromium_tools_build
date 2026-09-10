# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  path,
  platform,
  properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  path: path.API
  platform: platform.API
  properties: properties.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  test_specs = []
  if api.properties.get('local_gtest'):
    test_specs.append(steps.LocalGTestTestSpec.create('base_unittests'))
  if api.properties.get('swarming_gtest'):
    test_specs.append(steps.SwarmingGTestTestSpec.create('base_unittests'))
  if api.properties.get('local_isolated_script_test'):
    test_specs.append(
      steps.LocalIsolatedScriptTestSpec.create('base_unittests')
    )
  if api.properties.get('script_test'):
    test_specs.append(
      steps.ScriptTestSpec.create(
        'script_test',
        script='script.py',
        compile_targets=['compile_target'],
        script_args=['some', 'args'],
      )
    )
  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  api.chromium_tests.configure_build(builder_config)

  update_result = api.chromium_checkout.ensure_checkout()
  checkout_dir = update_result.checkout_dir
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)

  tests = [s.get_test(api.chromium_tests) for s in test_specs]
  with api.chromium_tests.wrap_chromium_tests(
    checkout_dir, source_dir, build_dir, tests=tests
  ):
    pass


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  test_builders = ctbc.BuilderDatabase.create(
    {
      'chromium.example': {
        'android-basic': ctbc.BuilderSpec.create(
          android_config='base_config',
          chromium_apply_config=[
            'mb',
          ],
          chromium_config='main_builder',
          chromium_config_kwargs={
            'BUILD_CONFIG': 'Release',
            'TARGET_ARCH': 'arm',
            'TARGET_BITS': 32,
            'TARGET_PLATFORM': 'android',
          },
          gclient_config='chromium',
          gclient_apply_config=['android'],
          simulation_platform='linux',
        ),
      },
    }
  )

  yield api.test(
    'require_device_steps',
    api.chromium_tests_builder_config.ci_build(
      builder_group='chromium.example',
      builder='android-basic',
      builder_db=test_builders,
    ),
    api.properties(local_gtest=True),
    api.post_process(post_process.MustRun, 'device_recovery'),
    api.post_process(post_process.MustRun, 'provision_devices'),
    api.post_process(post_process.MustRun, 'device_status'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'use_clang_coverage',
    api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'win',
    api.platform('win', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'isolated_targets',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-tester',
      parent_buildername='fake-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(swarming_gtest=True),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'local_isolated_script_test',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-tester',
      parent_buildername='fake-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(
      local_isolated_script_test=True,
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'script_test',
    api.platform('linux', 64),
    api.chromium.ci_build(
      builder_group='fake-group',
      builder='fake-tester',
      parent_buildername='fake-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .with_parent(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(script_test=True),
    api.post_process(post_process.DropExpectation),
  )
