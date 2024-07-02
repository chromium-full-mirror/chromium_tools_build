# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'recipe_engine/platform',
    'recipe_engine/properties',
]

def RunSteps(api):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  api.chromium_tests.configure_build(builder_config)
  update_step, _ = api.chromium_tests.prepare_checkout(builder_config)
  api.chromium_tests.download_and_unzip_build(
      builder_id, update_step, builder_config,
      **api.properties.get('kwargs', {}))


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
      'read-gn-args',
      api.platform('linux', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-tester',
          parent_buildername='fake-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_tester(
              builder_group='fake-group',
              builder='fake-tester',
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  build_gs_bucket='fake-gs-bucket',
              ),
          ).with_parent(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(kwargs=dict(read_gn_args=True)),
      api.post_process(post_process.MustRun, 'read GN args'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'do-not-read-gn-args',
      api.platform('linux', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-tester',
          parent_buildername='fake-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_tester(
              builder_group='fake-group',
              builder='fake-tester',
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  build_gs_bucket='fake-gs-bucket',
              ),
          ).with_parent(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(kwargs=dict(read_gn_args=False)),
      api.post_process(post_process.DoesNotRun, 'read GN args'),
      api.post_process(post_process.DropExpectation),
  )
