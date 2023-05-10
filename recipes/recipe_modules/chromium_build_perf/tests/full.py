# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from recipe_engine import post_process
from recipe_engine.recipe_api import Property

DEPS = [
    'builder_group',
    'chromium',
    'chromium_build_perf',
    'chromium_tests',
    'chromium_tests_builder_config',
    'recipe_engine/buildbucket',
    'reclient',
]


def RunSteps(api):
  builder_id = chromium.BuilderId.create_for_group(
      api.builder_group.for_current, api.buildbucket.builder_name)
  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
      builder_id, use_try_db=False)
  api.chromium_tests.configure_build(builder_config)

  api.chromium_build_perf.build('all', with_remote_cache=True)
  api.chromium_build_perf.build('all', with_remote_cache=False)
  api.chromium_build_perf.build(
      'all', with_remote_cache=False, step_name_suffix=' suffix')
  api.chromium_build_perf.build('all', with_remote_cache=False, revision='abcd')
  api.chromium_build_perf.remove_build_dir()
  api.chromium_build_perf.remove_deps_cache()


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config
  builder = {
      'builder_group': 'fake-group',
      'builder': 'fake-builder',
  }
  yield api.test(
      'full',
      api.chromium.ci_build(**builder),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  build_gs_bucket=None,
              ),
              **builder).assemble()),
      api.reclient.properties(),
      api.post_process(post_process.DropExpectation),
  )
