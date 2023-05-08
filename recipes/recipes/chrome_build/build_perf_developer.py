# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to measure developer build performance.
   See also go/chrome-developer-build-metrics
"""

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from recipe_engine import post_process

DEPS = [
    'builder_group',
    'chromium',
    'chromium_build_perf',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'reclient',
]


def RunSteps(api):
  # Set up a named cache so runhooks doesn't redownload everything on each run.
  solution_path = api.path['cache'].join('builder')
  api.file.ensure_directory('init cache if not exists', solution_path)

  # Checkout and gclient hooks.
  builder_id = chromium.BuilderId.create_for_group(
      api.builder_group.for_current, api.buildbucket.builder_name)
  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
      builder_id, use_try_db=False)
  api.chromium_tests.configure_build(builder_config)
  api.chromium_checkout.ensure_checkout()

  with api.context(cwd=solution_path):
    api.chromium.runhooks()

  # Build target: chrome or chrome_public_apk
  chrome_target = 'chrome'
  if builder_config.chromium_config == 'android':
    chrome_target = 'chrome_public_apk'
  raw_result = api.chromium_build_perf.clean_build(
      chrome_target, with_remote_cache=False)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  # TODO(b/270902505): add incremental builds.

  return raw_result


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  # Test data.
  builder = {
      'builder_group': 'fake-group',
      'builder': 'fake-builder',
  }

  yield api.test(
      'full_linux',
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
      api.post_process(post_process.StepSuccess,
                       'Build chrome without remote cache'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'full_android',
      api.chromium.ci_build(**builder),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  gclient_apply_config=['android'],
                  chromium_config='android',
                  build_gs_bucket=None,
              ),
              **builder).assemble()),
      api.reclient.properties(),
      api.post_process(post_process.StepSuccess,
                       'Build chrome_public_apk without remote cache'),
      api.post_process(post_process.DropExpectation),
  )

  for step in [
      'Build chrome without remote cache',
  ]:
    yield api.test(
        '%s_compile_fail' % step,
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
        api.step_data(step, retcode=1),
        api.expect_status('FAILURE'),
        api.post_process(post_process.DropExpectation),
    )
