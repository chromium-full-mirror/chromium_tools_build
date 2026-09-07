# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

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
from RECIPE_MODULES.recipe_engine import platform, properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  builder_id = api.chromium.get_builder_id()
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  api.chromium_tests.configure_build(builder_config)
  update_result, build_dir, targets_config = api.chromium_tests.prepare_checkout(
      builder_config)
  checkout_dir = update_result.checkout_dir
  source_dir = update_result.source_root.path
  affected_files = api.properties['affected_files']
  api.chromium_tests.determine_compilation_targets(
      builder_id,
      builder_config,
      source_dir,
      checkout_dir,
      build_dir,
      affected_files,
      targets_config,
  )


def GenTests(api: TEST_DEPS):

  def affected_spec_file():
    return sum([
        api.properties(affected_files=['testing/buildbot/fake-group.json']),
        api.chromium_tests_builder_config.try_build(
            builder_group='fake-try-group',
            builder='fake-try-builder',
            builder_db=ctbc.BuilderDatabase.create({
                'fake-group': {
                    'fake-builder':
                        ctbc.BuilderSpec.create(
                            chromium_config='chromium',
                            gclient_config='chromium',
                        ),
                },
            }),
            try_db=ctbc.TryDatabase.create({
                'fake-try-group': {
                    'fake-try-builder':
                        ctbc.TrySpec.create_for_single_mirror(
                            'fake-group', 'fake-builder'),
                },
            })),
    ], api.empty_test_data())

  for platform in ('linux', 'win'):
    yield api.test(
        'affected spec file {}'.format(platform),
        api.platform(platform, 64),
        affected_spec_file(),
        api.post_check(lambda check, steps: check(steps['analyze'].cmd == [])),
        api.post_process(post_process.DropExpectation),
    )

  ctbc_api = api.chromium_tests_builder_config
  yield api.test(
      'builder_config_additional_exclusions',
      api.platform('linux', 64),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder(
              additional_exclusions=['test.cc']).with_mirrored_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
              ).assemble()),
      api.properties(affected_files=['test.cc']),
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          }),
          try_db=ctbc.TryDatabase.create({
              'fake-try-group': {
                  'fake-try-builder':
                      ctbc.TrySpec.create_for_single_mirror(
                          'fake-group', 'fake-builder'),
              },
          })),
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.post_check(lambda check, steps: check(steps['analyze'].cmd == [])),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_builder',
      api.platform('linux', 64),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(affected_files=['test.cc']),
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
      api.chromium_tests.read_targets_spec('fake-group', {
          'fake-builder': {
              'gtest_tests': [{
                  'test': 'base_unittests',
              }],
          },
      }),
      api.post_check(post_process.DoesNotRun, 'analyze'),
      api.post_process(post_process.DropExpectation),
  )
