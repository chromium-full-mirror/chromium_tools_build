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
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'reclient',
]


def _rm_build_dir(api):
  api.file.rmtree('rmtree %s' % str(api.chromium.output_dir),
                  str(api.chromium.output_dir))


def _get_builder_id(api):
  buildername = api.buildbucket.builder_name
  return chromium.BuilderId.create_for_group(api.builder_group.for_current,
                                             buildername)


def _compile(api, targets, with_remote_cache):
  api.chromium.mb_gen(_get_builder_id(api), recursive_lookup=True)
  step_name = 'Build %s' % ','.join(targets)
  env = {}
  if with_remote_cache:
    step_name += ' with remote cache'
  else:
    step_name += ' without remote cache'
    env['RBE_remote_accept_cache'] = "false"
  try:
    with api.context(env=env):
      return api.chromium.compile(
          targets, name=step_name, use_goma_module=False, use_reclient=True)
  finally:
    _rm_build_dir(api)


def _compile_with_and_without_remote_cache(api, targets):
  # First build without remote cache.
  raw_result = _compile(api, targets, with_remote_cache=False)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  # Second build with remote cache produced by the previous build.
  return _compile(api, targets, with_remote_cache=True)


def RunSteps(api):
  # Set up a named cache so runhooks doesn't redownload everything on each run.
  solution_path = api.path['cache'].join('builder')
  api.file.ensure_directory('init cache if not exists', solution_path)

  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
      _get_builder_id(api), use_try_db=False)
  api.chromium_tests.configure_build(builder_config)

  api.chromium_checkout.ensure_checkout()

  if api.code_coverage.using_coverage:
    api.code_coverage.src_dir = api.chromium_checkout.src_dir
    api.code_coverage.instrument([])

  with api.context(cwd=solution_path):
    api.chromium.runhooks()

  _rm_build_dir(api)

  # Build target: all
  raw_result = _compile_with_and_without_remote_cache(api, ['all'])
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  # Build target: chrome or chrome_public_apk
  chrome_targets = ['chrome']
  if builder_config.chromium_config == 'android':
    chrome_targets = ['chrome_public_apk']
  return _compile_with_and_without_remote_cache(api, chrome_targets)


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
