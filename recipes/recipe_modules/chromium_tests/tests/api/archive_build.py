# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import (DoesNotRun, DropExpectation,
                                        StepCommandContains, StepSuccess)
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_tests, chromium_tests_builder_config
from RECIPE_MODULES.recipe_engine import (
    file,
    properties,
    raw_io,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  file: file.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  file: file.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  # Create a nested step so that setup steps can be easily filtered out
  with api.step.nest('setup steps'):
    builder_id, builder_config = (
        api.chromium_tests_builder_config.lookup_builder())
    api.chromium_tests.configure_build(builder_config)
    update_step, build_dir, _ = api.chromium_tests.prepare_checkout(
        builder_config)
  api.chromium_tests.archive_clusterfuzz(
      builder_id=builder_id,
      update_result=update_step,
      builder_config=builder_config,
      build_dir=build_dir,
      compile_targets=api.properties.get('compile_targets'),
  )
  api.chromium_tests.archive_build(build_dir, update_step)


def GenTests(api: TEST_DEPS):

  yield api.test(
      'cf_archive_build',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                          cf_archive_build=True,
                          cf_archive_name='cf_archive_build_test',
                          cf_gs_bucket='clusterfuzz-gs-bucket',
                          cf_gs_acl='public-read',
                      ),
              },
          })),
      api.post_process(StepCommandContains, 'gsutil upload', [
          'public-read',
          ('[CLEANUP]/cs/'
           'cf_archive_build_test-linux-release-170242.zip'),
          ('gs://clusterfuzz-gs-bucket/linux-release/'
           'cf_archive_build_test-linux-release-170242.zip'),
      ]),
      api.post_process(StepSuccess, 'gsutil upload'),
      api.post_process(StepSuccess,
                       'cf_archive_build_test-linux-release-170242.zip'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'cf_archive_schema_v1',
      api.properties(compile_targets=['fuzzer_target']),
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                          cf_archive_build=True,
                          cf_archive_name='cf_archive_build_test',
                          cf_gs_bucket='clusterfuzz-gs-bucket',
                          cf_gs_acl='public-read',
                          cf_archive_schema_version=1,
                      ),
              },
          })),
      api.step_data(
          'collect runtime deps for compile targets.'
          'read fuzzer_target.runtime_deps',
          api.file.read_text(
              './fuzzer_target\n../../testing/data/fuzzer_seed.txt\n'),
      ),
      api.post_process(StepCommandContains, 'zipping', [
          '["clusterfuzz_manifest.json", '
          '"out/ceb4-fake-builder/args.gn", '
          '"out/ceb4-fake-builder/fuzzer_target", '
          '"out/ceb4-fake-builder/fuzzer_target.runtime_deps", '
          '"testing/data/fuzzer_seed.txt"]'
      ]),
      api.post_process(StepSuccess, 'gsutil upload'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'cf_archive_build_tryserver',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                          cf_archive_build=True,
                          cf_archive_name='cf_archive_build_test',
                          cf_gs_bucket='clusterfuzz-gs-bucket',
                          cf_gs_acl='public-read',
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
      api.post_process(DoesNotRun, 'collect runtime deps for compile targets'),
      api.post_process(StepSuccess, 'zipping'),
      api.post_process(DoesNotRun, 'gsutil upload'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'cf_archive_build_verbatim',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                          cf_archive_build=True,
                          cf_archive_path='bleep-bloop/foo-bar',
                          cf_use_archive_path=True,
                          cf_gs_bucket='clusterfuzz-gs-bucket',
                          cf_gs_acl='public-read',
                      ),
              },
          })),
      api.post_process(StepCommandContains, 'gsutil upload', [
          'public-read',
          ('[CLEANUP]/cs/'
           'foo-bar-170242.zip'),
          ('gs://clusterfuzz-gs-bucket/bleep-bloop/foo-bar-170242.zip'),
      ]),
      api.post_process(StepSuccess, 'gsutil upload'),
      api.post_process(StepSuccess, 'foo-bar-170242.zip'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'cf_archive_build_assert_success',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                          cf_archive_build=True,
                          cf_archive_name='cf_archive_build_test',
                          cf_archive_path='linux-release/cf_archive_build_test-linux-release',
                          cf_use_archive_path=False,
                          cf_gs_bucket='clusterfuzz-gs-bucket',
                          cf_gs_acl='public-read',
                      ),
              },
          })),
      api.post_process(StepCommandContains, 'gsutil upload', [
          'public-read',
          ('[CLEANUP]/cs/'
           'cf_archive_build_test-linux-release-170242.zip'),
          ('gs://clusterfuzz-gs-bucket/linux-release/'
           'cf_archive_build_test-linux-release-170242.zip'),
      ]),
      api.post_process(StepSuccess, 'gsutil upload'),
      api.post_process(StepSuccess,
                       'cf_archive_build_test-linux-release-170242.zip'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'cf_archive_build_assert_failure',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                          cf_archive_build=True,
                          cf_archive_name='cf_archive_build_test',
                          cf_archive_path='wrong-path',
                          cf_use_archive_path=False,
                          cf_gs_bucket='clusterfuzz-gs-bucket',
                          cf_gs_acl='public-read',
                      ),
              },
          })),
      api.expect_exception('AssertionError'),
      api.post_process(DropExpectation),
  )
