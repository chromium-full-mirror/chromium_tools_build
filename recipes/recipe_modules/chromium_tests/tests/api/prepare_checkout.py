# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.recipe_engine import (
  file,
  json,
  path,
  platform,
  properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  file: file.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build import chromium_swarming
from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from PB.recipe_modules.build.chromium_tests.properties import InputProperties

DUMMY_BUILDERS = ctbc.BuilderDatabase.create(
  {
    'chromium.fake': {
      'cross-group-trigger-builder': ctbc.BuilderSpec.create(
        chromium_config='chromium',
        chromium_config_kwargs={
          'BUILD_CONFIG': 'Release',
        },
        gclient_config='chromium',
      ),
    },
    'chromium.fake.fyi': {
      'cross-group-trigger-tester': ctbc.BuilderSpec.create(
        execution_mode=ctbc.TEST,
        parent_buildername='cross-group-trigger-builder',
        parent_builder_group='chromium.fake',
      ),
    },
  }
)

PROPERTIES = {
  'runhooks_suffix': Property(default=None, kind=str),
}


def RunSteps(api: DEPS, runhooks_suffix):
  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  api.chromium_tests.configure_build(builder_config)
  api.chromium_tests.prepare_checkout(
    builder_config, runhooks_suffix=runhooks_suffix
  )


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
    'basic',
    api.platform('linux', 64),
    api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.post_process(post_process.MustRun, 'gclient runhooks'),
    api.post_process(post_process.DoesNotRun, 'gclient runhooks (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'fail-to-read-targets-spec',
    api.platform('linux', 64),
    api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.override_step_data(
      'read test spec (fake-group.json)',
      retcode=1,
    ),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'basic_try',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group', builder='fake-try-builder'
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.post_process(post_process.MustRun, 'gclient runhooks (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'without_patch',
    api.chromium.try_build(
      builder_group='fake-try-group', builder='fake-try-builder'
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(runhooks_suffix='without patch'),
    api.post_process(post_process.MustRun, 'gclient runhooks (without patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'has cache',
    api.platform('linux', 64),
    api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.step_data(
      'builder cache.check if empty', api.file.listdir(['foo', 'bar'])
    ),
    api.post_check(
      lambda check, steps: check(
        'builder cache is present' in steps['builder cache'].step_text
      )
    ),
    api.post_check(post_process.PropertyEquals, 'is_cached', True),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'does not have cache',
    api.platform('linux', 64),
    api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    ),
    api.step_data('builder cache.check if empty', api.file.listdir([])),
    api.post_check(
      lambda check, steps: check(
        'builder cache is absent' in steps['builder cache'].step_text
      )
    ),
    api.post_check(post_process.PropertyEquals, 'is_cached', False),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'cross_group_trigger',
    api.chromium_tests_builder_config.ci_build(
      builder_group='chromium.fake',
      builder='cross-group-trigger-builder',
      builder_db=DUMMY_BUILDERS,
    ),
    api.post_process(
      post_process.MustRun, 'read test spec (chromium.fake.fyi.json)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'mirror-with-non-child-tester',
    api.chromium_tests_builder_config.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            'fake-builder2': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
          },
          'fake-tester-group': {
            'fake-tester': ctbc.BuilderSpec.create(
              execution_mode=ctbc.TEST,
              parent_builder_group='fake-group',
              parent_buildername='fake-builder2',
            ),
          },
        }
      ),
      try_db=ctbc.TryDatabase.create(
        {
          'fake-try-group': {
            'fake-try-builder': ctbc.TrySpec.create(
              mirrors=[
                ctbc.TryMirror.create(
                  builder_group='fake-group',
                  buildername='fake-builder',
                  tester_group='fake-tester-group',
                  tester='fake-tester',
                )
              ]
            ),
          },
        }
      ),
    ),
    api.post_process(
      post_process.MustRun, 'read test spec (fake-tester-group.json)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'bad-spec',
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
          },
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': 'invalid-spec',
      },
    ),
    api.expect_exception('AttributeError'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'bad-spec-on-related-builder',
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            'fake-builder-with-bad-spec': ctbc.BuilderSpec.create(
              execution_mode=ctbc.TEST,
              chromium_config='chromium',
              gclient_config='chromium',
              parent_buildername='fake-builder',
            ),
          },
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {},
        'fake-builder-with-bad-spec': 'invalid-spec',
      },
    ),
    api.expect_exception('AttributeError'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'bad-spec-on-unrelated-builder',
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            'fake-builder-with-bad-spec': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
          },
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {},
        'fake-builder-with-bad-spec': 'invalid-spec',
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'test_trigger_cas',
    api.platform('linux', 64),
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-tester',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            'fake-tester': ctbc.BuilderSpec.create(
              execution_mode=ctbc.TEST,
              parent_buildername='fake-builder',
              use_test_trigger_cas=True,
              chromium_config='chromium',
              gclient_config='chromium',
            ),
          },
        }
      ),
    ),
    api.properties(
      test_trigger_deps_digest='fake-digest/123',
      parent_got_revision='fake-parent-revision',
    ),
    api.post_process(post_process.MustRun, 'download test trigger CAS'),
    api.post_process(post_process.DoesNotRun, 'bot_update'),
    api.post_process(post_process.DoesNotRun, 'gclient runhooks'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'test_trigger_cas_missing_digest',
    api.platform('linux', 64),
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-tester',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            'fake-tester': ctbc.BuilderSpec.create(
              execution_mode=ctbc.TEST,
              parent_buildername='fake-builder',
              use_test_trigger_cas=True,
              chromium_config='chromium',
              gclient_config='chromium',
            ),
          },
        }
      ),
    ),
    api.properties(
      parent_got_revision='fake-parent-revision',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
