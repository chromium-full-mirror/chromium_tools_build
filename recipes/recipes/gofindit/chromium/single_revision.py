# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.recipes.build.gofindit.chromium.single_revision import InputProperties
from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from recipe_engine.post_process import DropExpectation, MustRun, StepFailure

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
  findit,
  gofindit,
)
from RECIPE_MODULES.recipe_engine import properties


@dataclass
class DEPS(RecipeScriptApi):
  findit: findit.API
  gofindit: gofindit.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API


PROPERTIES = InputProperties


def RunSteps(api: DEPS, properties: InputProperties):
  target_builder_id = chromium_types.BuilderId.create_for_group(
    properties.target_builder.group, properties.target_builder.builder
  )
  # TODO: replace this with the polymorphic API when it is ready (go/test-reviver-builders-dd)
  builder_config = api.findit.get_builder_config(target_builder_id)
  return api.gofindit.single_revision_steps(
    builder_id=builder_config.builder_ids[0],
    builder_config=builder_config,
    compile_targets=properties.compile_targets,
    analysis_id=properties.analysis_id,
    bisection_host=properties.bisection_host,
    should_clobber=properties.should_clobber,
  )


def GenTests(api: TEST_DEPS):

  def setup(
    api: TEST_DEPS,
    target_builder_group='fake-group',
    target_builder='fake-builder',
    should_clobber=False,
  ):
    """Create test properties and other data for tests."""
    _default_spec = 'fake-group', {'fake-builder': {}}
    _default_builders = ctbc.BuilderDatabase.create(
      {
        'fake-group': {
          'fake-builder': ctbc.BuilderSpec.create(
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

    props_proto = InputProperties()
    props_proto.target_builder.group = target_builder_group
    props_proto.target_builder.builder = target_builder
    props_proto.should_clobber = should_clobber

    t = sum(
      [
        api.chromium.ci_build(
          builder_group=target_builder_group,
          builder=target_builder,
        ),
        api.properties(props_proto),
        api.chromium_tests.read_targets_spec(*_default_spec),
        api.chromium_tests_builder_config.databases(_default_builders),
      ],
      api.empty_test_data(),
    )
    return t

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
