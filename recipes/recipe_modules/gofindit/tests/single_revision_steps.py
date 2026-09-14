# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from dataclasses import dataclass

from recipe_engine.post_process import DropExpectation, MustRun, StepFailure
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
  gofindit,
)
from RECIPE_MODULES.recipe_engine import properties, step


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  gofindit: gofindit.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API
  step: step.TEST_API


def RunSteps(api: DEPS):
  builder_id, builder_config = (
    api.chromium_tests_builder_config.lookup_builder()
  )
  compile_targets = api.properties.get('compile_targets')
  should_clobber = api.properties.get('should_clobber', False)
  analysis_id = api.properties.get('analysis_id', 123)
  bisection_host = api.properties.get(
    'bisection_host', 'luci-bisection.appspot.com'
  )

  return api.gofindit.single_revision_steps(
    builder_id=builder_id,
    builder_config=builder_config,
    compile_targets=compile_targets,
    analysis_id=analysis_id,
    bisection_host=bisection_host,
    should_clobber=should_clobber,
  )


def GenTests(api: TEST_DEPS):

  def setup(
    api: TEST_DEPS,
    target_builder_group='fake-group',
    target_builder='fake-builder',
    should_clobber=False,
    compile_targets=None,
  ):
    _default_spec = target_builder_group, {target_builder: {}}
    _default_builders = ctbc.BuilderDatabase.create(
      {
        target_builder_group: {
          target_builder: ctbc.BuilderSpec.create(
            chromium_config='chromium',
            chromium_apply_config=['mb'],
            gclient_config='chromium',
            chromium_config_kwargs={
              'BUILD_CONFIG': 'Release',
              'TARGET_BITS': 64,
            },
            simulation_platform='linux',
          ),
        },
      }
    )

    props = {
      'should_clobber': should_clobber,
    }
    if compile_targets:
      props['compile_targets'] = compile_targets

    return sum(
      [
        api.chromium.ci_build(
          builder_group=target_builder_group,
          builder=target_builder,
        ),
        api.properties(**props),
        api.chromium_tests.read_targets_spec(*_default_spec),
        api.chromium_tests_builder_config.databases(_default_builders),
      ],
      api.empty_test_data(),
    )

  yield api.test(
    'compile',
    setup(api),
    api.post_process(MustRun, 'bot_update'),
    api.post_process(MustRun, 'compile'),
    api.post_process(MustRun, 'send_result_to_luci_bisection'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'compile with clobber',
    setup(api, should_clobber=True),
    api.post_process(MustRun, 'bot_update'),
    api.post_process(MustRun, 'clobber'),
    api.post_process(MustRun, 'compile'),
    api.post_process(MustRun, 'send_result_to_luci_bisection'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'compile failure',
    setup(api),
    api.step_data('compile', retcode=1),
    api.expect_status('FAILURE'),
    api.post_process(MustRun, 'bot_update'),
    api.post_process(MustRun, 'generate_build_files'),
    api.post_process(MustRun, 'compile'),
    api.post_process(MustRun, 'send_result_to_luci_bisection'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'generate_build_files failure',
    setup(api),
    api.step_data('generate_build_files', retcode=1),
    api.expect_status('FAILURE'),
    api.post_process(StepFailure, 'generate_build_files'),
    api.post_process(MustRun, 'bot_update'),
    api.post_process(MustRun, 'generate_build_files'),
    api.post_process(MustRun, 'send_result_to_luci_bisection'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'compile specific targets',
    setup(api, compile_targets=['chrome', 'browser_tests']),
    api.post_process(MustRun, 'bot_update'),
    api.post_process(MustRun, 'compile'),
    api.post_process(MustRun, 'send_result_to_luci_bisection'),
    api.post_process(DropExpectation),
  )
