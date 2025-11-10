# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Creates index for history-rag."""
from recipe_engine import post_process
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'build/chromium',
    'build/chromium_checkout',
    'build/chromium_tests',
    'build/chromium_tests_builder_config',
    'depot_tools/gsutil',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/properties',
]


def checkout_source_code(api):
  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  api.chromium_tests.configure_build(builder_config)
  update_result = api.chromium_checkout.ensure_checkout()
  checkout_dir = update_result.checkout_dir
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  api.chromium.ensure_toolchains(checkout_dir)
  api.chromium.runhooks(source_dir, build_dir, clobber=True)


def RunSteps(api):
  checkout_source_code(api)


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          revision='newrevision',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          chromium_apply_config=['mb'],
                          gclient_config='chromium',
                      ),
              },
          })),
      api.post_process(post_process.MustRun, 'gclient runhooks'),
      api.post_process(post_process.DropExpectation),
  )
