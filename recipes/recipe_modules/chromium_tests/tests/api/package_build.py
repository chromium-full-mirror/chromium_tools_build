# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'recipe_engine/properties',
]


def RunSteps(api):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  api.chromium_tests.configure_build(builder_config)
  update_step, _, _ = api.chromium_tests.prepare_checkout(builder_config)
  api.chromium_tests.package_build(
      builder_id, update_step, builder_config, reasons=['for test coverage'])


def GenTests(api):
  yield api.test(
      'standard',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.fake',
          builder='fake-builder',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.fake': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          build_gs_bucket='sample-bucket',
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.post_process(post_process.DoesNotRun, 'package build for bisect'),
      api.post_process(post_process.MustRun, 'package build'),
      api.post_process(
          post_process.StepCommandContains, 'package build',
          ['--build-url', 'gs://sample-bucket/chromium.fake/fake-builder']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'perf-upload',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.perf',
          builder='fake-perf-builder',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.perf': {
                  'fake-perf-builder':
                      ctbc.BuilderSpec.create(
                          build_gs_bucket='sample-bucket',
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.post_process(post_process.DoesNotRun, 'package build for bisect'),
      api.post_process(post_process.MustRun, 'package build'),
      api.post_process(post_process.StepCommandContains, 'package build',
                       ['--build-url', 'gs://sample-bucket/fake-perf-builder']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'bisect',
      api.chromium_tests_builder_config.ci_build(
          builder_group='chromium.perf',
          builder='fake-bisect-builder',
          builder_db=ctbc.BuilderDatabase.create({
              'chromium.perf': {
                  'fake-bisect-builder':
                      ctbc.BuilderSpec.create(
                          bisect_archive_build=True,
                          bisect_gs_bucket='sample-bisect-bucket',
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              },
          })),
      api.post_process(post_process.MustRun, 'package build for bisect'),
      api.post_process(post_process.DoesNotRun, 'package build'),
      api.post_process(
          post_process.StepCommandContains, 'package build for bisect',
          ['--build-url', 'gs://sample-bisect-bucket/fake-bisect-builder']),
      api.post_process(post_process.DropExpectation),
  )
