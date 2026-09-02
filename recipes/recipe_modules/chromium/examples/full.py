# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, repro_instructions, siso
from RECIPE_MODULES.depot_tools import bot_update, gclient
from RECIPE_MODULES.recipe_engine import (
    context,
    json,
    path,
    platform,
    properties,
    raw_io,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  chromium: chromium.API
  context: context.API
  gclient: gclient.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  repro_instructions: repro_instructions.API
  siso: siso.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  siso: siso.TEST_API


def RunSteps(api: DEPS):
  resource_usage_output_file = api.properties.get('resource_usage_output_file',
                                                  None)

  include_bot_update_instruction = api.properties.get(
      'include_bot_update_instruction', False)
  if include_bot_update_instruction:
    api.repro_instructions.add_step_instruction(api.step.empty('bot_update'))

  api.gclient.set_config('chromium')
  for c in api.properties.get('gclient_configs', []):
    api.gclient.apply_config(c)
  api.chromium.set_config('chromium',
                          **api.properties.get('chromium_config_kwargs', {}))
  for c in api.properties.get('chromium_configs', []):
    api.chromium.apply_config(c)

  assert api.chromium.build_properties == None

  with api.chromium.chromium_layout():
    builder_id = api.chromium.get_builder_id()

    api.chromium.get_build_target_arch()

    with api.context(cwd=api.path.cache_dir / 'builder'):
      update_result = api.bot_update.ensure_checkout()

    checkout_dir = update_result.checkout_dir
    source_dir = update_result.source_root.path
    build_dir = api.chromium.default_build_dir(source_dir)

    api.chromium.ensure_toolchains(checkout_dir)
    api.chromium.runhooks(source_dir, build_dir)

    mb_config_path = api.properties.get('mb_config_path')

    api.chromium.mb_gen(
        source_dir,
        build_dir,
        builder_id,
        mb_config_path=mb_config_path,
        android_version_code=3,
        android_version_name="example")

    return api.chromium.compile(
        source_dir,
        build_dir,
        targets=['All'],
        resource_usage_output_file=resource_usage_output_file,
        include_utr_instruction=True,
        builder_id=builder_id)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
  )

  yield api.test(
      'custom_mb_config',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.properties(mb_config_path='/custom/config.pyl'),
  )

  yield api.test(
      'siso',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.siso.properties(),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ninja_build_failure',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'ninja_no_op_failure',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.override_step_data(
          'compile confirm no-op',
          api.raw_io.output_text(
              "ninja explain: chrome is dirty\n", name='failure_summary'),
          stdout=api.raw_io.output_text("ninja explain: chrome is dirty\n"),
      ),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'measure_resource_usage',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.properties(resource_usage_output_file=api.path.cache_dir /
                     'resource_usage' / 'time_log.txt'),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--resource_usage_output_file']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'mac_basic',
      api.platform('mac', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.post_process(post_process.StepSuccess, 'ensure_installed'),
      api.post_process(post_process.StepCommandContains, 'ensure_installed', [
          'infra/tools/mac_toolchain/${platform} '
          'git_revision:07e67ff89ff11ed0e3071867ed6bb49319a91b05'
      ]),
  )

  yield api.test(
      'mac_toolchain',
      api.platform('mac', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.properties(chromium_configs=['mac_toolchain']),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'mac_toolchain_properties',
      api.platform('mac', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.properties(
          chromium_configs=['mac_toolchain'],
          xcode_build_version='12345',
      ),
  )

  yield api.test(
      'chromium_tvos',
      api.platform('mac', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.properties(
          chromium_configs=['chromium_tvos'],
          xcode_build_version='12345',
      ),
  )

  yield api.test(
      'mac_with_metal_toolchain',
      api.platform('mac', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.properties(
          chromium_configs=['mac_toolchain'],
          xcode_build_version='12345',
          with_metal_toolchain=True,
      ),
      api.post_process(post_process.StepCommandContains,
                       'ensure xcode.install xcode', ['-with-metal-toolchain']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'with_mac_toolchain_version',
      api.platform('mac', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.properties(chromium_configs=['mac_toolchain'],),
      api.chromium.properties(
          mac_toolchain_version='custom_version',
          xcode_build_version='12345',
      ),
      api.post_process(
          post_process.StepCommandContains, 'ensure xcode.ensure_installed',
          ['infra/tools/mac_toolchain/${platform} git_revision:custom_version'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  # Coverage for the chromeos gclient config defined in this module
  yield api.test(
      'chromeos',
      api.platform('linux', 64),
      api.chromium.ci_build(
          builder_group='chromium.chromiumos',
          builder='chromeos-amd64-generic-rel',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.properties(
          gclient_configs=['chromeos'],
          chromium_config_kwargs={
              'BUILD_CONFIG': 'Release',
              'TARGET_ARCH': 'intel',
              'TARGET_BITS': 64,
              'TARGET_PLATFORM': 'chromeos',
              'CROS_BOARDS_WITH_QEMU_IMAGES': 'amd64-generic-vm',
          },
      ),
  )

  yield api.test(
      'shared_build_dir',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
      ),
      api.properties(chromium_configs=['shared_build_dir'],),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'build_dir_name',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder-with-really-long-name',
      ),
      api.path.exists(
          api.path.cache_dir.joinpath('builder', 'src', 'out',
                                      '7e94-fake-builder-with-really-')),
      # The arg after '-C' on the cmd line should be the path to the build-dir.
      api.post_check(post_process.StepCommandContains, 'compile',
                     ['-C', re.compile('.+fake-builder')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'runhooks_instruction_tag',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      api.properties(include_bot_update_instruction=True),
      api.post_process(post_process.DropExpectation),
  )
