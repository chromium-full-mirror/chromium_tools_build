# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to measure build time.
   See also go/chrome-build-time
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
    'recipe_engine/step',
    'reclient',
]


def _raise_raw_result_on_failure(api, raw_result):
  if raw_result.status != common_pb.SUCCESS:
    raise api.step.StepFailure(raw_result.summary_markdown)


def _compile_without_remote_execution(api, target, resource_usage_output_dir):
  # Build without remote execution.
  api.chromium_build_perf.recreate_build_dir(remove_deps_cache=True)
  raw_result = api.chromium_build_perf.build_with_ninja(
      target,
      with_remote_cache=False,
      use_rbe=False,
      resource_usage_output_dir=resource_usage_output_dir)
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

  api.chromium_checkout.ensure_checkout()
  api.chromium.ensure_toolchains(
      checkout_dir=api.chromium_checkout.checkout_dir)

  with api.context(cwd=solution_path):
    api.chromium.runhooks()

  resource_usage_output_dir = api.path.cache_dir / 'resource_usage'
  api.file.ensure_directory('init resource usage dir if not exists',
                            resource_usage_output_dir)

  # Build target: chrome
  _compile_without_remote_execution(api, 'chrome', resource_usage_output_dir)


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
      api.post_process(post_process.StepCommandContains,
                       'Build chrome without remote execution',
                       ['--resource_usage_output']),
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
      api.reclient.properties(),
      api.step_data('Build chrome without remote execution', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
