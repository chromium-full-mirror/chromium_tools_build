# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to measure build step performance.
   See also go/build-perf-builder
"""

from recipe_engine import post_process
from recipe_engine.config_types import Path

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
    'profiles',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'reclient',
]


def _raise_raw_result_on_failure(api, raw_result):
  if raw_result.status != common_pb.SUCCESS:
    raise api.step.StepFailure(raw_result.summary_markdown)


def _compile_with_and_without_remote_cache(
    api,
    source_dir: Path,
    build_dir: Path,
    target,
):
  # First build without remote cache.
  api.chromium_build_perf.recreate_build_dir(
      source_dir, build_dir, remove_deps_cache=True)
  raw_result = api.chromium_build_perf.build_with_ninja(
      source_dir, build_dir, target, with_remote_cache=False)
  _raise_raw_result_on_failure(api, raw_result)

  # Second build with remote cache produced by the previous build.
  api.chromium_build_perf.recreate_build_dir(source_dir, build_dir)
  raw_result = api.chromium_build_perf.build_with_ninja(
      source_dir, build_dir, target, with_remote_cache=True)
  _raise_raw_result_on_failure(api, raw_result)


def RunSteps(api):
  # Set up a named cache so runhooks doesn't redownload everything on each run.
  solution_path = api.path.cache_dir / 'builder'
  api.file.ensure_directory('init cache if not exists', solution_path)

  builder_id = chromium.BuilderId.create_for_group(
      api.builder_group.for_current, api.buildbucket.builder_name)
  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
      builder_id, use_try_db=False)
  api.chromium_tests.configure_build(builder_config)

  if api.reclient.instance:
    api.reclient.use_download_remoteexec_cfg_hook(api.gclient.c.solutions[0])

  update_result = api.chromium_checkout.ensure_checkout()
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  api.chromium.ensure_toolchains(checkout_dir=update_result.checkout_dir)

  if api.code_coverage.using_coverage:
    api.profiles.source_dir = source_dir
    api.code_coverage.source_dir = source_dir
    api.code_coverage.build_dir = build_dir
    api.code_coverage.instrument([])

  with api.context(cwd=solution_path):
    api.chromium.runhooks(source_dir, build_dir)

  # Build target: all
  _compile_with_and_without_remote_cache(api, source_dir, build_dir, 'all')

  # Remove the out dir to reduce the builder cache size.
  api.file.rmtree('rmtree %s' % str(build_dir), str(build_dir))


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
              ),
              **builder).assemble()),
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
              ),
              **builder).assemble()),
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
              ),
              **builder).assemble()),
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
              ),
              **builder).assemble()),
      api.reclient.properties(),
      api.step_data('Build all without remote cache', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
