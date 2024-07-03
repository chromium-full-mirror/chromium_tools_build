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
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'chromium_utr',
    'recipe_engine/buildbucket',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
]

PROPERTIES = Request


def RunSteps(api: RecipeApi, properties: Request):
  try:
    builder_id, builder_config = configure_build(
        api, properties.checkout_path, properties.run_type
        != Request.RunType.RUN_TYPE_RUN,
        properties.rerun_options.skip_config_validation)
  except BadConf as e:
    rerun_options = [
        api.chromium_utr.create_prompt_option(
            properties, 'yes', skip_config_validation=True),
        api.chromium_utr.create_prompt_option(properties, 'no')
    ]
    err_msg = (f'Caution: the recipe config for this builder may be '
               f'incompatible with the current machine: {e.args[0]}. Continue?')
    return api.chromium_utr.create_rerun_result(
        rerun_options, err_msg, properties.output_properties_file)

  return api.chromium_utr.run(properties, builder_id, builder_config)


def configure_build(
    api: RecipeApi,
    checkout_dir: str,
    build: bool,
    skip_validation: bool,
) -> tuple[chromium.BuilderId, ctbc.BuilderConfig]:
  """Prepares the recipe to build with the provided checkout.

  Args:
      api: Recipe API object.
      checkout_dir: String to a chromium/src checkout that already has all
        intended updates or syncs.
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
  builder_id = chromium.BuilderId.create_for_group(
      api.m.properties['builder_group'], builder)
  _, builder_config = (
      api.chromium_tests_builder_config.lookup_builder(use_try_db=True))

  api.chromium.verify_config = not skip_validation
  api.chromium_tests.configure_build(builder_config, test_only=not build)

  api.chromium_checkout.set_paths(api.path.cache_dir,
                                  api.path.abs_to_path(checkout_dir))
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
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
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
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                          chromium_config_kwargs={
                              'TARGET_PLATFORM': 'ios',
                          },
                      ),
              },
          })),
      api.post_process(
          post_process.SummaryMarkdown,
          'Caution: the recipe config for this builder may be incompatible '
          'with the current machine: Can not compile "ios" on "linux". '
          'Continue?'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
