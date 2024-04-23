# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to measure siso build step performance.
"""

from recipe_engine import post_process
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
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'reclient',
    'siso',
]


def _raise_raw_result_on_failure(api, raw_result):
  if raw_result.status != common_pb.SUCCESS:
    raise api.step.StepFailure(raw_result.summary_markdown)


def _get_builder_id(api):
  buildername = api.buildbucket.builder_name
  return chromium.BuilderId.create_for_group(api.builder_group.for_current,
                                             buildername)

def _run_builds(api, target, phase, step_name_suffix=None):
  # First build without remote cache.
  api.chromium_build_perf.recreate_build_dir(
      phase=phase, remove_deps_cache=True)
  raw_result = api.chromium_build_perf.build_with_siso(
      target, with_remote_cache=False, step_name_suffix=step_name_suffix)
  _raise_raw_result_on_failure(api, raw_result)

  # Warm-up the remote cache when not using reproxy, because otherwise
  # C++ actions will not get cache hits due to their deps changing
  # after parsing the depsfile. TODO(b/283341125)
  if phase == 'builtin':
    api.chromium_build_perf.recreate_build_dir(phase=phase)
    raw_result = api.chromium_build_perf.build_with_siso(
        target, with_remote_cache=True, step_name_suffix=' (warmup)')

  # Second build with remote cache produced by the previous build.
  api.chromium_build_perf.recreate_build_dir(phase=phase)
  raw_result = api.chromium_build_perf.build_with_siso(
      target, with_remote_cache=True, step_name_suffix=step_name_suffix)
  _raise_raw_result_on_failure(api, raw_result)


def RunSteps(api):
  # Set up a named cache so runhooks doesn't redownload everything on each run.
  solution_path = api.path.cache_dir / 'builder'
  api.file.ensure_directory('init cache if not exists', solution_path)

  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
      _get_builder_id(api), use_try_db=False)
  api.chromium_tests.configure_build(builder_config)

  api.chromium_checkout.ensure_checkout()
  api.chromium.ensure_toolchains(
      checkout_dir=api.chromium_checkout.checkout_dir)

  if api.code_coverage.using_coverage:
    api.code_coverage.src_dir = api.chromium_checkout.src_dir
    api.code_coverage.instrument([])
  with api.context(cwd=solution_path):
    api.chromium.runhooks()

  api.step('check siso version', [api.siso.siso_path, 'version'])

  # Build target: all
  _run_builds(api, 'all', phase='builtin')
  _run_builds(api, 'all', phase='reproxy', step_name_suffix=' with reproxy')


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
      api.siso.properties(),
      api.reclient.properties(),
      api.code_coverage(use_clang_coverage=True),
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
      api.siso.properties(),
      api.reclient.properties(),
      api.code_coverage(use_clang_coverage=True),
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
      api.siso.properties(),
      api.reclient.properties(),
      api.code_coverage(use_clang_coverage=True),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'build_failure',
      api.chromium.ci_build(**builder),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  build_gs_bucket=None,
              ),
              **builder).assemble()),
      api.siso.properties(),
      api.reclient.properties(),
      api.step_data('Build all without remote cache', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
