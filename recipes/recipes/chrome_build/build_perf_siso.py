# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to measure siso build step performance.
"""

from datetime import datetime, timedelta

from recipe_engine import post_process
from recipe_engine.config_types import Path

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build import chromium_types
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
    'depot_tools/git',
    'profiles',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'reclient',
    'siso',
]


def _raise_raw_result_on_failure(api, raw_result):
  if raw_result.status != common_pb.SUCCESS:
    raise api.step.StepFailure(raw_result.summary_markdown)


def _get_builder_id(api):
  buildername = api.buildbucket.builder_name
  return chromium_types.BuilderId.create_for_group(
      api.builder_group.for_current, buildername)


def _clean_builds(api, source_dir: Path, build_dir: Path, target: str):
  # Do not run clean build for CI benchmarking since it's too slow for
  # small -remote_jobs value.
  if api.siso.project.startswith("rbe-chromium-trusted"):
    return

  with api.step.nest('Clean builds'):
    # Build target: all
    _run_clean_builds(api, source_dir, build_dir, target, phase='builtin')



def _incremental_build_with_one_hour_changes(
    api,
    source_dir: Path,
    default_build_dir: Path,
    target,
):
  """Steps to run an incremental build with 1-hour of changes
     to simulate CI/CQ builder's incremental builds.
  """
  time_format = '%Y-%m-%d %H:%M:%S %z'

  with api.step.nest('Incremental build with 1-hour of changes'):
    cur_rev = api.buildbucket.gitiles_commit.id or 'HEAD'
    cur_rev_at = api.git(
        'show',
        '--quiet',
        '--format=%ci',
        cur_rev,
        stdout=api.raw_io.output_text(),
        step_test_data=lambda: api.raw_io.test_api.stream_output_text(
            '2023-05-02 11:28:30 +0000\n')).stdout.strip()
    cur_rev_at = datetime.strptime(cur_rev_at, time_format)

    base_rev = api.git(
        'log',
        '--pretty=%H',
        '--since="%s"' %
        (cur_rev_at - timedelta(hours=1)).strftime(time_format),
        '--reverse',
        stdout=api.raw_io.output_text(),
        step_test_data=lambda: api.raw_io.test_api.stream_output_text(
            'abcd\nefgh\n')).stdout.split()[0]

    # Clean up deps cache and check out to the base revision.
    api.chromium_build_perf.checkout(source_dir, default_build_dir, base_rev)

    # Run a warm up build for local build dir.
    ## Default
    api.chromium_build_perf.recreate_build_dir(
        source_dir, default_build_dir, phase='builtin', remove_deps_cache=True)
    raw_result = api.chromium_build_perf.build_with_siso(
        source_dir,
        default_build_dir,
        target,
        with_remote_cache=True,
        step_name_suffix=' at base revision (warmup)')
    _raise_raw_result_on_failure(api, raw_result)


    # Incremental build with remote caches at the current revision.
    api.chromium_build_perf.checkout(source_dir, default_build_dir, cur_rev)

    ## Default
    raw_result = api.chromium_build_perf.build_with_siso(
        source_dir, default_build_dir, target, with_remote_cache=True)
    _raise_raw_result_on_failure(api, raw_result)





def _run_clean_builds(api,
                      source_dir: Path,
                      build_dir: Path,
                      target,
                      phase,
                      step_name_suffix=None):
  # First build without remote cache.
  api.chromium_build_perf.recreate_build_dir(
      source_dir, build_dir, phase=phase, remove_deps_cache=True)
  resource_usage_output_file = None
  if phase == 'builtin' and api.platform.is_linux:
    resource_usage_output_file = api.path.mkstemp()
  raw_result = api.chromium_build_perf.build_with_siso(
      source_dir,
      build_dir,
      target,
      with_remote_cache=False,
      step_name_suffix=step_name_suffix,
      resource_usage_output_file=resource_usage_output_file,
  )
  _raise_raw_result_on_failure(api, raw_result)

  if resource_usage_output_file:
    rusage = api.file.read_json(
        'read resource usage log',
        resource_usage_output_file,
        test_data={'ru_utime': '0:01.00'},
    )
    api.chromium_build_perf.upload_build_stats_to_bq(rusage)

  # Second build with remote cache produced by the previous build.
  api.chromium_build_perf.recreate_build_dir(source_dir, build_dir, phase=phase)
  raw_result = api.chromium_build_perf.build_with_siso(
      source_dir,
      build_dir,
      target,
      with_remote_cache=True,
      step_name_suffix=step_name_suffix)
  _raise_raw_result_on_failure(api, raw_result)

  # Enable fail-on-bad-deps as an additional step on Linux for continuous
  # benchmarking before enabling by default. See crbug.com/547682173.
  if api.platform.is_linux:
    api.chromium_build_perf.recreate_build_dir(
        source_dir, build_dir, phase=phase)
    # Do not fail the overall build if fail-on-bad-deps fails, but the step
    # itself will still be marked as a step failure. See crbug.com/556013464.
    api.chromium_build_perf.build_with_siso(
        source_dir,
        build_dir,
        target,
        with_remote_cache=True,
        step_name_suffix=' with Siso with fail-on-bad-deps',
        siso_experiments=['fail-on-bad-deps'])



def RunSteps(api):
  # Set up a named cache so runhooks doesn't redownload everything on each run.
  solution_path = api.path.cache_dir / 'builder'
  api.file.ensure_directory('init cache if not exists', solution_path)
  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
      _get_builder_id(api), use_try_db=False)
  api.chromium_tests.configure_build(builder_config)

  update_result = api.chromium_checkout.ensure_checkout()
  api.chromium.ensure_toolchains(checkout_dir=update_result.checkout_dir)
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)

  if api.code_coverage.using_coverage:
    api.profiles.source_dir = source_dir
    api.code_coverage.source_dir = source_dir
    api.code_coverage.build_dir = build_dir
    api.code_coverage.instrument([])
  with api.context(cwd=solution_path):
    api.chromium.runhooks(source_dir, build_dir)

  api.step('check siso version', [api.siso.siso_path(source_dir), 'version'])

  target = 'all'
  _clean_builds(api, source_dir, build_dir, target)

  _incremental_build_with_one_hour_changes(api, source_dir, build_dir, target)

  # Remove the out dir to reduce the builder cache size.
  out_dir = build_dir.parent
  api.file.rmtree('rmtree %s' % str(out_dir), str(out_dir))

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
              ),
              **builder).assemble()),
      api.siso.properties(),
      api.reclient.properties(),
      api.code_coverage(use_clang_coverage=True),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci',
      api.chromium.ci_build(**builder),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
              ),
              **builder).assemble()),
      api.siso.properties(project="rbe-chromium-trusted"),
      api.reclient.properties(),
      api.code_coverage(use_clang_coverage=True),
      api.post_process(post_process.DoesNotRun,
                       'Clean builds.Build all without remote cache'),
      api.post_process(post_process.DoesNotRun,
                       'Clean builds.Build all with remote cache'),
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
      api.siso.properties(),
      api.reclient.properties(),
      api.step_data('Clean builds.Build all without remote cache', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
