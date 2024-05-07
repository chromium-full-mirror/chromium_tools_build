# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Triggers building and running tests for the Universal Test Runner"""

from recipe_engine import post_process
from recipe_engine.config_types import Path
from recipe_engine.recipe_api import RecipeApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.recipe_modules.build.chromium_utr.request import Request
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests_builder_config import (
    builder_config as builder_config_module)

DEPS = [
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'chromium_utr',
    'recipe_engine/buildbucket',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
]

PROPERTIES = Request


def RunSteps(api: RecipeApi, properties: Request):
  compiling_builder_id, compiling_builder_config = (
      configure_build(api, properties.checkout_path, properties.run_type
                      != Request.RunType.RUN_TYPE_RUN))

  return api.chromium_utr.run(properties, compiling_builder_id,
                              compiling_builder_config)


def configure_build(
    api: RecipeApi, checkout_dir: str,
    build: bool) -> tuple[chromium.BuilderId, ctbc.BuilderConfig, Path, Path]:
  """Prepares the recipe to build with the provided checkout.

  Args:
      api: Recipe API object.
      checkout_dir: String to a chromium/src checkout that already has all
        intended updates or syncs.
      build: Bool to configure the run to include compiling/building.

  Returns:
    Tuple of
      BuilderId for the compiler builder,
      BuilderConfig for the compiling builder
  """
  builder = api.buildbucket.build.builder.builder
  builder_id = chromium.BuilderId.create_for_group(
      api.m.properties['builder_group'], builder)
  _, builder_config = (
      api.chromium_tests_builder_config.lookup_builder(use_try_db=True))

  # Default to assuming the builder both compiles and tests. But if it's
  # test-only, then we need to fetch its parent configs for use with mb/GN.
  compiling_builder_config = builder_config
  compiling_builder_id = builder_id
  if builder_config.execution_mode == ctbc.TEST:
    compiling_builder_id = chromium.BuilderId.create_for_group(
        builder_config.parent_builder_group, builder_config.parent_buildername)
    compiling_builder_config = builder_config_module.BuilderConfig.lookup(
        compiling_builder_id, builder_config.builder_db)
    if compiling_builder_config.execution_mode != ctbc.COMPILE_AND_TEST:
      raise api.step.StepFailure(
          f'Unsupported UTR invocation for builder {builder} triggered by '
          f'builder {builder_config.parent_buildername}. Please file a general '
          "infra bug via https://g.co/bugatrooper if you're seeing this.")

  api.chromium_tests.configure_build(builder_config, test_only=not build)
  api.path.checkout_dir = api.path.abs_to_path(checkout_dir)
  api.chromium_checkout.checkout_dir = api.path.cache_dir
  return compiling_builder_id, compiling_builder_config


def GenTests(api: RecipeTestApi):

  def boilerplate_properties():
    return api.properties(
        checkout_path='[CACHE]/src',
        run_type=Request.RunType.RUN_TYPE_COMPILE_AND_RUN,
        rerun_options=Request.RerunOptions(bypass_gclient=True,),
    )

  yield api.test(
      'basic',
      boilerplate_properties(),
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
      'unsupported_child_testers',
      boilerplate_properties(),
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake-group',
          builder='fake-grandchild-tester',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
                  'fake-child-tester':
                      ctbc.BuilderSpec.create(
                          execution_mode=ctbc.TEST,
                          parent_buildername='fake-builder',
                          parent_builder_group='fake-group',
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
                  'fake-grandchild-tester':
                      ctbc.BuilderSpec.create(
                          execution_mode=ctbc.TEST,
                          parent_buildername='fake-child-tester',
                          parent_builder_group='fake-group',
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.post_process(
          post_process.SummaryMarkdownRE,
          'Unsupported UTR invocation for builder fake-grandchild-tester.*',
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
