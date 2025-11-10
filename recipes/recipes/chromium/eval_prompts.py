# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe for running gemini-cli prompt evaluations from chromium/src/agents"""

from recipe_engine import post_process
from recipe_engine.recipe_api import RecipeApi
from recipe_engine.recipe_test_api import RecipeTestApi

DEPS = [
    'builder_group',
    'chromium',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/resultdb',
    'recipe_engine/step',
]

GOOGLE_CLOUD_PROJECT = 'chrome-ci-vertex-provider'
GOOGLE_CLOUD_LOCATION = 'us-central1'

NODEJS_CIPD_TAG = 'version:2@20.10.0'


def RunSteps(api: RecipeApi):
  update_result = checkout(api)

  packages_root = api.path.start_dir / 'packages'
  _ensure_nodejs(api, packages_root)

  source_dir = update_result.source_root.path
  with api.context(
      cwd=source_dir,
      env={
          'GOOGLE_GENAI_USE_VERTEXAI': 'true',
          'GOOGLE_CLOUD_PROJECT': GOOGLE_CLOUD_PROJECT,
          'GOOGLE_CLOUD_LOCATION': GOOGLE_CLOUD_LOCATION,
      },
      env_suffixes={
          'PATH': [
              api.path.cleanup_dir,
              packages_root / 'bin',
              source_dir / 'third_party' / 'depot_tools',
          ]
      }):

    _install_gemini_cli(api)

    # TODO(crbug.com/445459853): Get 3pp promptfoo when available. At the
    # moment, the test runner pulls and builds it itself.

    api.step('gemini-cli version', ['gemini', '-v'])

    # Evals are written with the expectation that depot_tools is on the path.
    # The third_party/depot_tools shouldn't be updated though so use
    # ensure_bootstrap to init the repo.
    api.step('bootstrap depot_tools', ['ensure_bootstrap'])

    # Run the stable tests that will fail the build first
    _run_tests(api, update_result, source_dir, True)

    # Run the unstable tests that we just want to collect metrics for
    _run_tests(api, update_result, source_dir, False)


def checkout(api: RecipeApi):
  _ensure_btrfs_subvolume(api, api.chromium_checkout.default_checkout_dir)
  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  api.chromium_tests.configure_build(builder_config)

  api.chromium_tests.check_builder_cache(
      api.chromium_checkout.default_checkout_dir)

  update_result = api.chromium_checkout.ensure_checkout()
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  api.chromium.runhooks(source_dir, build_dir)

  return update_result


def _ensure_btrfs_subvolume(api, path):
  with api.step.nest('ensure btrfs subvolume', status='last'):
    if not _is_btrfs_subvolume(api, path):
      api.file.rmtree('remove non-btrfs checkout dir', path)
      api.step('create btrfs subvolume', ['btrfs', 'subvolume', 'create', path])


def _is_btrfs_subvolume(api, path):
  return api.step(
      'check btrfs subvolume',
      ['sudo', 'btrfs', 'subvolume', 'show', path],
      raise_on_failure=False,
  ).retcode == 0


def _run_tests(api, update_result, source_dir, stable):
  build = api.buildbucket.build
  cmd = [
      'vpython3',
      source_dir.joinpath('agents', 'testing', 'eval_prompts.py'),
      '-f',
      '-v',
      '--parallel-workers',
      -1,
  ]
  if not api.tryserver.is_tryserver:
    cmd.extend([
        # TODO(b/449818513): Re-enable uploading when the new perf dashboard
        # instance is available and the test runner has been moved away
        # from Chrome's hierarchy-based reporting.
        # '--enable-perf-uploading',
        '--git-revision',
        # The perf dashboard infra normally accepts a git revision, but the
        # Chrome-related instances use commit position for historical reasons.
        'CP:%s' % update_result.out_commit.position,
        '--gcs-bucket',
        # Despite the name, this only contains public data and is safe to
        # access from public builders.
        # TODO(b/450054252): Switch this to the primary Chrome bucket
        # once we confirm uploading is working as intended.
        'chrome-perf-experiment-non-public',
        '--build-id',
        build.id,
        '--builder',
        build.builder.builder,
        '--builder-group',
        api.builder_group.for_current,
        '--build-number',
        build.number,
    ])
  if stable:
    step_name = 'run stable eval prompts'
    cmd += ['--tag-filter=stable']
    ok_ret = (0,)
  else:
    step_name = 'run unstable eval prompts'
    cmd += ['--tag-filter=-stable']
    ok_ret = 'any'
  api.step(
      step_name,
      api.resultdb.wrap(
          cmd,
          module_name='//agents/testing:eval_prompts',
          module_scheme='flat',
      ),
      ok_ret=ok_ret,
  )


def _ensure_nodejs(api, cipd_root):
  ef = api.cipd.EnsureFile()
  ef.add_package(
      'infra/3pp/tools/nodejs/linux-${arch}',
      NODEJS_CIPD_TAG,
  )
  api.cipd.ensure(cipd_root, ef, 'ensure nodejs')


def _install_gemini_cli(api):
  # TODO(crbug.com/445459853): This is hacky and only a temporary workaround.
  # This should be replaced with the 3pp cipd package when that is available
  path = api.path.cleanup_dir / 'gemini'
  api.file.write_text(
      'write env script',
      path,
      '#!/usr/bin/env sh\n'
      'npx https://github.com/google-gemini/gemini-cli "$@"',
  )
  api.file.chmod('chmod file', path, '755')


def GenTests(api: RecipeTestApi):
  ctbc_api = api.chromium_tests_builder_config

  def gen_ci_test_props():
    return api.chromium.ci_build(
        builder_group='fake-group',
        builder='fake-builder',
    ) + ctbc_api.properties(
        ctbc_api.properties_assembler_for_ci_builder(
            builder_group='fake-group',
            builder='fake-builder',
        ).assemble())

  def gen_try_test_props():
    return api.chromium.try_build(
        builder_group='fake-group',
        builder='fake-builder',
    ) + ctbc_api.properties(
        ctbc_api.properties_assembler_for_ci_builder(
            builder_group='fake-group',
            builder='fake-builder',
        ).assemble())

  yield api.test(
      'basic',
      gen_ci_test_props(),
      api.post_process(post_process.MustRun, 'run stable eval prompts'),
      api.post_process(post_process.MustRun, 'run unstable eval prompts'),
      # TODO(b/449818513): Change this to StepCommandContains when perf
      # uploading is re-enabled.
      api.post_process(post_process.StepCommandDoesNotContain,
                       'run stable eval prompts', ['--enable-perf-uploading']),
      api.post_process(post_process.StepCommandContains,
                       'run stable eval prompts', ['fake-builder']),
      api.post_process(post_process.StepCommandContains,
                       'run stable eval prompts', ['fake-group']),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'run unstable eval prompts',
                       ['--enable-perf-uploading']),
      api.post_process(post_process.StepCommandContains,
                       'run unstable eval prompts', ['fake-builder']),
      api.post_process(post_process.StepCommandContains,
                       'run unstable eval prompts', ['fake-group']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_try',
      gen_try_test_props(),
      api.post_process(post_process.MustRun, 'run stable eval prompts'),
      api.post_process(post_process.MustRun, 'run unstable eval prompts'),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'run stable eval prompts', ['--enable-perf-uploading']),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'run unstable eval prompts',
                       ['--enable-perf-uploading']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'fail',
      gen_ci_test_props(),
      api.step_data('run stable eval prompts', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'fail unstable',
      gen_ci_test_props(),
      api.step_data('run unstable eval prompts', retcode=1),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'not-a-btrfs-subvolume',
      gen_ci_test_props(),
      api.step_data('ensure btrfs subvolume.check btrfs subvolume', retcode=1),
      api.post_process(post_process.DropExpectation),
  )
