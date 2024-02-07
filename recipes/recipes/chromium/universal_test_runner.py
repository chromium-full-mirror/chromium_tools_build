# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Triggers building and running tests for the Universal Test Runner"""

from recipe_engine.config_types import Path, BasePath

from PB.recipes.build.chromium.universal_test_runner import InputProperties
from RECIPE_MODULES.build import chromium

DEPS = [
    'code_coverage',
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'pgo',
    'recipe_engine/buildbucket',
    'recipe_engine/cas',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
]

PROPERTIES = InputProperties


class RootBasePath(BasePath):
  """A base path for the root of the filesystem."""

  def resolve(self, test_enabled: bool) -> str:
    return ''


def RunSteps(api, properties):
  builder_id, builder_config = configure_build(api, properties)
  # TODO(crbug.com/1519709): Conditionally disable code coverage
  tests = create_tests(api, properties, builder_id, builder_config)
  # TODO(crbug.com/1519709): Conditionally skip testing
  api.chromium_tests.run_tests(builder_id, builder_config, tests)


def create_tests(api, properties, builder_id, builder_config):
  targets_config = api.chromium_tests.create_targets_config(
      builder_config,
      properties.got_revisions,
      api.path['checkout'],
  )
  tests = [
      test for test in targets_config.all_tests
      if test.name in properties.tests_to_run
  ]
  # TODO(crbug.com/1519709): Conditionally compile these tests
  isolate_tests = [test for test in tests if test.isolate_target]
  if isolate_tests:
    mb_args = ['--no-build']
    build_dir = properties.build_dir or f'//out/{api.chromium.c.build_config_fs}'
    mb_args.append(build_dir)
    mb_args.extend([test.target_name for test in tests if test.isolate_target])
    api.chromium.run_mb_cmd(
        'isolate',
        'isolate',
        builder_id,
        additional_args=mb_args,
    )
    api.chromium_tests.isolate_tests(builder_config, isolate_tests, '', '')
  return tests


def configure_build(api, properties):
  builder = api.buildbucket.build.builder.builder
  builder_id = chromium.BuilderId.create_for_group(
      api.m.properties['builder_group'], builder)
  _, builder_config = (
      api.chromium_tests_builder_config.lookup_builder(use_try_db=True))
  # TODO(crbug.com/1519709): test_only should be based on run mode
  api.chromium_tests.configure_build(builder_config, test_only=True)
  src_checkout = Path(RootBasePath(), properties.checkout_path)
  api.path['checkout'] = src_checkout
  return builder_id, builder_config


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  def ctbc_properties(builder_spec=None):
    return ctbc_api.properties(
        ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
            builder_group='fake-group',
            builder='fake-builder',
            builder_spec=builder_spec,
        ).with_mirrored_tester(
            builder_group='fake-group',
            builder='fake-tester',
        ).assemble())

  yield api.test(
      'basic',
      ctbc_properties(),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-tester': {
                  'gtest_tests': [{
                      'name': 'browser_tests',
                      'swarming': {
                          'dimensions': {
                              'os': 'Linux',
                          },
                      },
                  }],
              },
          }),
      api.chromium.generic_build(
          builder_group='fake-group',
          builder='fake-tester',
      ),
      api.properties(
          tests_to_run=['browser_tests'],
          test_suite='fake_tests',
          builder_group='fake-group',
          checkout_path='checkout'),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', '', failures=['Test.One']),
  )
