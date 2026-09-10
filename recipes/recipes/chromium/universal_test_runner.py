# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Triggers building and running tests for the Universal Test Runner"""

from recipe_engine import post_process
from recipe_engine.config import BadConf
from recipe_engine.config_types import Path
from recipe_engine.recipe_api import RecipeApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.recipe_modules.build.chromium_utr.request import Request
from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
  chromium_tests,
  chromium_tests_builder_config,
  chromium_utr,
  dawn,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  path,
  platform,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  chromium_utr: chromium_utr.API
  dawn: dawn.API
  path: path.API
  platform: platform.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API


PROPERTIES = Request


def RunSteps(api: RecipeApi, properties: Request):
  source_dir = api.path.cast_to_path(properties.checkout_path)
  checkout_dir = source_dir.parent
  api.chromium_checkout.set_paths(checkout_dir, source_dir)
  try:
    builder_id, builder_config = configure_build(
      api,
      properties.run_type != Request.RunType.RUN_TYPE_RUN,
      properties.rerun_options.skip_config_validation,
    )
  except BadConf as e:
    rerun_options = [
      api.chromium_utr.create_prompt_option(
        properties, 'yes', skip_config_validation=True
      ),
      api.chromium_utr.create_prompt_option(properties, 'no'),
    ]
    err_msg = (
      f'Caution: the recipe config for this builder may be '
      f'incompatible with the current machine: {e.args[0]}. Continue?'
    )
    return api.chromium_utr.create_rerun_result(
      rerun_options, err_msg, properties.output_properties_file
    )

  return api.chromium_utr.run(
    properties, checkout_dir, source_dir, builder_id, builder_config
  )


def configure_build(
  api: DEPS,
  build: bool,
  skip_validation: bool,
) -> tuple[chromium_types.BuilderId, ctbc.BuilderConfig]:
  """Prepares the recipe to build with the provided checkout.

  Args:
      api: Recipe API object.
      build: Bool to configure the run to include compiling/building.
      skip_validation: Bool to prevent setting of config from raising a BadConf
        exception

  Returns:
    Tuple of
      BuilderId for the builder,
      BuilderConfig for the builder

  Raises:
    BadConf: When skip_validation is false, this can be thrown if the config
      is determined to not be compatible with the current running enviornment
  """
  builder = api.buildbucket.build.builder.builder
  builder_id = chromium_types.BuilderId.create_for_group(
    api.properties['builder_group'], builder
  )
  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
    use_try_db=True
  )

  api.chromium.verify_config = not skip_validation
  api.chromium_tests.configure_build(builder_config, test_only=not build)

  # A tester builder needs to explicitly check the platform for its parent
  # builder. This check is normally done in the config validation.
  if build and api.chromium.c.TEST_ONLY and not skip_validation:
    _, compiling_config = api.chromium_utr.get_compiling_builder_config(
      builder_id, builder_config
    )
    api.chromium.make_config(
      compiling_config.chromium_config,
      **compiling_config.chromium_config_kwargs,
    )

  return builder_id, builder_config


def GenTests(api: RecipeTestApi):

  yield api.test(
    'basic',
    api.properties(
      checkout_path='[CACHE]/src',
      run_type=Request.RunType.RUN_TYPE_COMPILE_AND_RUN,
      rerun_options=Request.RerunOptions(bypass_gclient=True),
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
            ),
          },
        }
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'tester_builder_validate_parent_chromium_config',
    api.properties(
      checkout_path='[CACHE]/src',
      run_type=Request.RunType.RUN_TYPE_COMPILE_AND_RUN,
      rerun_options=Request.RerunOptions(bypass_gclient=True),
    ),
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
              parent_builder_group='fake-group',
              chromium_config='chromium',
              gclient_config='chromium',
            ),
          },
        }
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'checkout_to_anypath',
    api.properties(
      checkout_path='/not/known/to/recipe/engine/src',
      run_type=Request.RunType.RUN_TYPE_COMPILE_AND_RUN,
      rerun_options=Request.RerunOptions(bypass_gclient=True),
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
            ),
          },
        }
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'bad_config',
    api.properties(
      checkout_path='[CACHE]/src',
      run_type=Request.RunType.RUN_TYPE_COMPILE_AND_RUN,
      rerun_options=Request.RerunOptions(bypass_gclient=True),
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
                'TARGET_PLATFORM': 'ios',
              },
            ),
          },
        }
      ),
    ),
    api.post_process(
      post_process.SummaryMarkdown,
      'Caution: the recipe config for this builder may be incompatible '
      'with the current machine: Unexpectedly attempting to compile ios '
      'from linux. The chromium config HOST_PLATFORM was not set '
      'explicitly. Continue?',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
