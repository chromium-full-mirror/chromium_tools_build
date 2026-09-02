# Copyright 2024 The Chromium Authors. All rights reserved.
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
    chromium_polymorphic,
    chromium_reviver,
    chromium_tests,
    chromium_tests_builder_config,
    code_coverage,
    test_utils,
)
from RECIPE_MODULES.recipe_engine import raw_io


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_polymorphic: chromium_polymorphic.API
  chromium_reviver: chromium_reviver.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  code_coverage: code_coverage.API
  raw_io: raw_io.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_polymorphic: chromium_polymorphic.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  code_coverage: code_coverage.TEST_API
  raw_io: raw_io.TEST_API
  test_utils: test_utils.TEST_API


def RunSteps(api: DEPS):
  return api.chromium_reviver.run()


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
      'basic',
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group=None,
      ),
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'fake-gtest',
                  }],
                  'scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                  }],
              },
          }),
      api.post_check(post_process.StepCommandContains, 'compile',
                     ['fake-gtest']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['--gtest_also_run_disabled_tests']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['-var', 'reviver_project:fake-project']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['-var', 'reviver_bucket:fake-bucket']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['-var', 'reviver_builder:fake-builder']),
      api.post_check(post_process.DoesNotRun, 'fake-script-test'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'legacy-builder-config',
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group=None,
      ),
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.databases(
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          gclient_config='chromium',
                          chromium_config='chromium',
                      ),
              },
          })),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'fake-gtest',
                  }],
                  'scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                  }],
              },
          }),
      api.post_check(post_process.StepCommandContains, 'compile',
                     ['fake-gtest']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['--gtest_also_run_disabled_tests']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['-var', 'reviver_project:fake-project']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['-var', 'reviver_bucket:fake-bucket']),
      api.post_check(post_process.StepCommandContains, 'fake-gtest',
                     ['-var', 'reviver_builder:fake-builder']),
      api.post_check(post_process.DoesNotRun, 'fake-script-test'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'compile-failure',
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group=None,
      ),
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'fake-gtest',
                  }],
                  'scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                  }],
              },
          }),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test-failure',
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group=None,
      ),
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'fake-gtest',
                  }],
                  'scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                  }],
              },
          }),
      api.override_step_data(
          'fake-gtest results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'fake-gtest', failing_tests=['foo', 'bar']))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test-infra-failure',
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group=None,
      ),
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'fake-gtest',
                  }],
                  'scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                  }],
              },
          }),
      api.override_step_data('fake-gtest', retcode=1),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'process-coverage',
      api.code_coverage(use_clang_coverage=True),
      api.chromium.generic_build(
          project='reviver-project',
          bucket='reviver-bucket',
          builder='fake-runner',
          builder_group=None,
      ),
      api.chromium_polymorphic.triggered_properties(
          project='fake-project',
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder='fake-builder',
              builder_group='fake-group',
          ).assemble()),
      api.chromium_tests.read_targets_spec(
          'fake-group', {
              'fake-builder': {
                  'gtest_tests': [{
                      'test': 'fake-gtest',
                  }],
                  'scripts': [{
                      'name': 'fake-script-test',
                      'script': 'fake-script',
                  }],
              },
          }),
      api.post_process(post_process.MustRunRE, '.*coverage data.*'),
      api.post_process(post_process.DropExpectation),
  )
