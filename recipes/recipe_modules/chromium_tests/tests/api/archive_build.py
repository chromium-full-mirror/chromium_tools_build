# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DropExpectation, StatusSuccess,
                                        StepCommandContains, StepSuccess)
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'recipe_engine/step',
]


def RunSteps(api):
  # Create a nested step so that setup steps can be easily filtered out
  with api.step.nest('setup steps'):
    builder_id, builder_config = (
        api.chromium_tests_builder_config.lookup_builder())
    api.chromium_tests.configure_build(builder_config)
    update_step, _ = api.chromium_tests.prepare_checkout(builder_config)
  api.chromium_tests.archive_build(builder_id, update_step, builder_config)


def GenTests(api):

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
          ('[CLEANUP]/chrome_staging/'
           'cf_archive_build_test-linux-release-170242.zip'),
          ('gs://clusterfuzz-gs-bucket/linux-release/'
           'cf_archive_build_test-linux-release-170242.zip'),
      ]),
      api.post_process(StepSuccess, 'gsutil upload'),
      api.post_process(StepSuccess,
                       'cf_archive_build_test-linux-release-170242.zip'),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )
