# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to measure build step performance.
   See also go/build-perf-builder
"""

from recipe_engine import post_process
from recipe_engine.engine_types import freeze
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'builder_group',
    'chromium',
    'chromium_build_perf',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'reclient',
]


def _compile_with_and_without_remote_cache(api, target):
  # First build without remote cache.
  api.chromium_build_perf.remove_build_dir()
  raw_result = api.chromium_build_perf.build(target, with_remote_cache=False)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  # Second build with remote cache produced by the previous build.
  api.chromium_build_perf.remove_build_dir()
  return api.chromium_build_perf.build(target, with_remote_cache=True)


def RunSteps(api):
  # Set up a named cache so runhooks doesn't redownload everything on each run.
  solution_path = api.path['cache'].join('builder')
  api.file.ensure_directory('init cache if not exists', solution_path)

  builder_id = chromium.BuilderId.create_for_group(
      api.builder_group.for_current, api.buildbucket.builder_name)
  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
      builder_id, use_try_db=False)
  api.chromium_tests.configure_build(builder_config)

  api.chromium_checkout.ensure_checkout()

  if api.code_coverage.using_coverage:
    api.code_coverage.src_dir = api.chromium_checkout.src_dir
    api.code_coverage.instrument([])

  with api.context(cwd=solution_path):
    api.chromium.runhooks()

  # Build target: all
  raw_result = _compile_with_and_without_remote_cache(api, 'all')
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  # Build target: chrome or chrome_public_apk
  chrome_target = 'chrome'
  if builder_config.chromium_config == 'android':
    chrome_target = 'chrome_public_apk'
  return _compile_with_and_without_remote_cache(api, chrome_target)


def _sanitize_nonalpha(text):
  return ''.join(c if c.isalnum() else '_' for c in text)


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
      api.code_coverage(use_clang_coverage=True),
      api.post_process(post_process.StepSuccess,
                       'Build all without remote cache'),
      api.post_process(post_process.StepSuccess, 'Build all with remote cache'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome without remote cache'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome with remote cache'),
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
      api.code_coverage(use_clang_coverage=True),
      api.post_process(post_process.StepSuccess,
                       'Build all without remote cache'),
      api.post_process(post_process.StepSuccess, 'Build all with remote cache'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome_public_apk without remote cache'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome_public_apk with remote cache'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'full_windows',
      api.platform('win', 64),
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
      api.code_coverage(use_clang_coverage=True),
      api.post_process(post_process.StepSuccess,
                       'Build all without remote cache'),
      api.post_process(post_process.StepSuccess, 'Build all with remote cache'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome without remote cache'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome with remote cache'),
      api.post_process(post_process.DropExpectation),
  )

  for step in [
      'Build all without remote cache', 'Build all with remote cache',
      'Build chrome without remote cache', 'Build chrome with remote cache'
  ]:
    yield api.test(
        '%s_compile_fail' % (_sanitize_nonalpha(step)),
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
