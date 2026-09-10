# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Checks out and compiles src at ToT and writes freshness info to warmed.txt"""

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipes.build.chromium.builder_cache_prewarmer import InputProperties
from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
  chromium_tests,
  chromium_tests_builder_config,
  code_coverage,
)
from RECIPE_MODULES.depot_tools import bot_update
from RECIPE_MODULES.recipe_engine import (
  file,
  json,
  path,
  platform,
  properties,
  step,
  time,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  code_coverage: code_coverage.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  step: step.API
  time: time.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  code_coverage: code_coverage.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  time: time.TEST_API


PROPERTIES = InputProperties

warmed_file_name = 'warmed.txt'


def RunSteps(api: DEPS, properties):
  with api.chromium.chromium_layout():
    cache_dir = api.path.cache_dir / 'builder'
    api.file.rmglob('delete warmed.txt', cache_dir, warmed_file_name)

    builder_id = chromium_types.BuilderId.create_for_group(
      properties.builder_to_warm.builder_group,
      properties.builder_to_warm.builder_name,
    )

    _, builder_config = api.chromium_tests_builder_config.lookup_builder(
      builder_id=builder_id, use_try_db=True
    )

    # (crbug/1273173) Only the mirrored builder and not the mirrored tester
    # will be reported
    api.chromium_tests.report_builders(builder_config)

    api.chromium_tests.configure_build(builder_config)
    update_result, build_dir, targets_config = (
      api.chromium_tests.prepare_checkout(builder_config, no_fetch_tags=True)
    )

    # Get timestamp before compiling since that could take a while
    checkout_time = int(api.time.time())

    if api.code_coverage.using_coverage:
      api.code_coverage.instrument([])

    raw_result, _ = api.chromium_tests.compile_specific_targets(
      build_dir,
      builder_id,
      builder_config,
      update_result,
      targets_config,
      targets_config.compile_targets,
      targets_config.all_tests,
    )

    if raw_result and raw_result.status != common_pb.SUCCESS:
      return raw_result

    build_revision = update_result.properties.get(
      'got_revision', update_result.properties.get('got_src_revision')
    )

    warmed_path = cache_dir / warmed_file_name
    api.file.write_text(
      'write warmed.txt',
      warmed_path,
      '{},{}'.format(checkout_time, build_revision),
    )

    # TODO (kimstephanie): chmod doesn't work on windows so either remove or
    # replace this
    api.step('Set read access on warmed file', ['chmod', '444', warmed_path])

    # TODO (kimstephanie): Move above logic into a loop that exits when there
    # are pending builds


def GenTests(api: TEST_DEPS):

  def override_test_spec():
    return api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'Fake Builder': {
          'scripts': [
            {
              "isolate_profile_data": True,
              "name": "check_static_initializers",
              "script": "check_static_initializers.py",
            }
          ],
        },
        'Fake Tests': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'swarming': {},
            }
          ],
        },
      },
    )

  fake_revision = 'd3adv3ggi3'
  fake_timestamp = 1637279060

  _BUILDER_DB = ctbc.BuilderDatabase.create(
    {
      'fake-group': {
        # TODO (gbeaty): Shouldn't be necessary to create a spec for
        # 'Warmer' because it's using the passed in try builder config
        'Warmer': ctbc.BuilderSpec.create(
          chromium_config='chromium',
          gclient_config='chromium',
        ),
        'Fake Builder': ctbc.BuilderSpec.create(
          chromium_config='chromium',
          gclient_config='chromium',
        ),
        'Fake Tests': ctbc.BuilderSpec.create(
          chromium_config='chromium',
          gclient_config='chromium',
          execution_mode=ctbc.TEST,
          parent_buildername='Fake Builder',
        ),
      },
    }
  )

  _TRY_DB = ctbc.TryDatabase.create(
    {
      'fake-try-group': {
        'fake-try-builder': ctbc.TrySpec.create_for_single_mirror(
          builder_group='fake-group',
          buildername='Fake Builder',
          tester='Fake Tests',
        ),
      }
    }
  )

  yield api.test(
    'basic',
    api.chromium_tests_builder_config.generic_build(
      builder_group='fake-group',
      builder='Warmer',
      try_db=_TRY_DB,
      builder_db=_BUILDER_DB,
      use_try_db=True,
    ),
    api.properties(
      InputProperties(
        builder_to_warm=InputProperties.Builder(
          builder_name='fake-try-builder', builder_group='fake-try-group'
        )
      )
    ),
    api.code_coverage(use_clang_coverage=True),
    override_test_spec(),
    api.time.seed(fake_timestamp),
    api.time.step(0),
    api.bot_update.revisions({'src': fake_revision}),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(
      post_process.MustRun, 'delete {}'.format(warmed_file_name)
    ),
    api.post_process(
      post_process.StepCommandContains,
      'write {}'.format(warmed_file_name),
      ['{},{}'.format(fake_timestamp, fake_revision)],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'compile_failed',
    api.chromium_tests_builder_config.generic_build(
      builder_group='fake-group',
      builder='Warmer',
      try_db=_TRY_DB,
      builder_db=_BUILDER_DB,
      use_try_db=True,
    ),
    api.platform.name('linux'),
    api.code_coverage(use_clang_coverage=True),
    api.properties(
      InputProperties(
        builder_to_warm=InputProperties.Builder(
          builder_name='fake-try-builder', builder_group='fake-try-group'
        )
      )
    ),
    override_test_spec(),
    api.override_step_data('compile', retcode=1),
    api.post_process(
      post_process.DoesNotRun, 'write {}'.format(warmed_file_name)
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
