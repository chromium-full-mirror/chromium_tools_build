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
    'siso',
]


def _rm_build_dir(api):
  api.file.rmtree('rmtree %s' % str(api.chromium.output_dir),
                  str(api.chromium.output_dir))


def _get_builder_id(api):
  buildername = api.buildbucket.builder_name
  return chromium.BuilderId.create_for_group(api.builder_group.for_current,
                                             buildername)


def _compile(api, step_name, target, with_remote_cache):
  # TODO(b/253142009): support Windows with Siso native builds.
  if api.platform.is_win:
    return
  api.chromium.mb_gen(
      _get_builder_id(api), recursive_lookup=True, phase='builtin')

  ninja_command = ['ninja', '-C', api.chromium.output_dir, target]
  siso_args = []
  if not with_remote_cache:
    api.file.rmtree('rmtree %s' % api.siso.deps_log,
                  api.siso.deps_log)
    siso_args += ['-re_cache_enable_read=false']
  try:
    with api.context(cwd=api.path['checkout']):
      return api.siso.run_ninja(
          ninja_command=ninja_command, name=step_name, siso_args=siso_args)
  finally:
    _rm_build_dir(api)


def _compile_with_reproxy(api, step_name, target, with_remote_cache):
  api.chromium.mb_gen(
      _get_builder_id(api), recursive_lookup=True, phase='reproxy')

  ninja_command = ['ninja', '-C', api.chromium.output_dir, target]

  env = {}
  if not with_remote_cache:
    api.file.rmtree('rmtree %s' % api.reclient.deps_cache_path,
                    api.reclient.deps_cache_path)
    env['RBE_remote_accept_cache'] = "false"

  step_name += ' with reproxy'
  try:
    with api.context(cwd=api.path['checkout'], env=env):
      with api.reclient.process(step_name, None):
        return api.siso.run_ninja(ninja_command=ninja_command, name=step_name)
  finally:
    _rm_build_dir(api)


def _run_builds(api, target, with_reproxy=None):
  # First build without remote cache.
  step_name = 'Build %s without remote cache' % target
  if with_reproxy:
    raw_result = _compile_with_reproxy(
        api, step_name, target, with_remote_cache=False)
  else:
    raw_result = _compile(api, step_name, target, with_remote_cache=False)
  if raw_result and raw_result.status != common_pb.SUCCESS:
    return raw_result

  # Second build with remote cache produced by the previous build.
  step_name = 'Build %s with remote cache' % target
  if with_reproxy:
    raw_result = _compile_with_reproxy(
        api, step_name, target, with_remote_cache=True)
  else:
    raw_result = _compile(api, step_name, target, with_remote_cache=True)
  return raw_result


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
  raw_result = _run_builds(api, 'all', with_reproxy=False)
  if raw_result and raw_result.status != common_pb.SUCCESS:
    return raw_result

  raw_result = _run_builds(api, 'all', with_reproxy=True)
  if raw_result and raw_result.status != common_pb.SUCCESS:
    return raw_result

  # Build target: chrome or chrome_public_apk
  target_chrome = 'chrome'
  if builder_config.chromium_config == 'android':
    target_chrome = 'chrome_public_apk'
  raw_result = _run_builds(api, target_chrome, with_reproxy=False)
  if raw_result and raw_result.status != common_pb.SUCCESS:
    return raw_result

  return _run_builds(api, target_chrome, with_reproxy=True)


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
      api.siso.properties(),
      api.reclient.properties(),
      api.code_coverage(use_clang_coverage=True),
      api.post_process(post_process.StepSuccess,
                       'Build all without remote cache'),
      api.post_process(post_process.StepSuccess, 'Build all with remote cache'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome without remote cache'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome with remote cache'),
      api.post_process(post_process.StepSuccess,
                       'Build all without remote cache with reproxy'),
      api.post_process(post_process.StepSuccess,
                       'Build all with remote cache with reproxy'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome without remote cache with reproxy'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome with remote cache with reproxy'),
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
      api.post_process(post_process.StepSuccess,
                       'Build all without remote cache'),
      api.post_process(post_process.StepSuccess, 'Build all with remote cache'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome_public_apk without remote cache'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome_public_apk with remote cache'),
      api.post_process(post_process.StepSuccess,
                       'Build all without remote cache with reproxy'),
      api.post_process(post_process.StepSuccess,
                       'Build all with remote cache with reproxy'),
      api.post_process(
          post_process.StepSuccess,
          'Build chrome_public_apk without remote cache with reproxy'),
      api.post_process(
          post_process.StepSuccess,
          'Build chrome_public_apk with remote cache with reproxy'),
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
      api.post_process(post_process.StepSuccess,
                       'Build all without remote cache with reproxy'),
      api.post_process(post_process.StepSuccess,
                       'Build all with remote cache with reproxy'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome without remote cache with reproxy'),
      api.post_process(post_process.StepSuccess,
                       'Build chrome with remote cache with reproxy'),
      api.post_process(post_process.DropExpectation),
  )

  for target in ['all', 'chrome']:
    for with_remote_cache in [True, False]:
      for with_reproxy in [True, False]:
        step = 'Build %s %s remote cache' % (target, 'with' if with_remote_cache
                                             else 'without')
        if with_reproxy:
          step += ' with reproxy'

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
            api.siso.properties(),
            api.reclient.properties(),
            api.step_data(step, retcode=1),
            api.expect_status('FAILURE'),
            api.post_process(post_process.DropExpectation),
        )
