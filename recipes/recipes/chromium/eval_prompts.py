# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe for running gemini-cli prompt evaluations from chromium/src/agents"""

from recipe_engine import post_process
from recipe_engine.recipe_api import RecipeApi
from recipe_engine.recipe_test_api import RecipeTestApi
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.recipe_engine import result as result_pb2

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    builder_group,
    chromium,
    chromium_checkout,
    chromium_tests,
    chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cipd,
    context,
    file,
    path,
    properties,
    resultdb,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  cipd: cipd.API
  context: context.API
  file: file.API
  path: path.API
  properties: properties.API
  resultdb: resultdb.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  builder_group: builder_group.TEST_API
  chromium: chromium.TEST_API
  chromium_checkout: chromium_checkout.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  cipd: cipd.TEST_API
  file: file.TEST_API
  path: path.TEST_API
  resultdb: resultdb.TEST_API
  step: step.TEST_API
  tryserver: tryserver.TEST_API

GOOGLE_CLOUD_PROJECT = 'chrome-ci-vertex-provider'
GOOGLE_CLOUD_LOCATION = 'global'

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
    status = common_pb2.SUCCESS
    try:
      _run_tests(api, update_result, source_dir, True)

      # Run the unstable tests that we just want to collect metrics for
      # TODO(b/503830579): Re-enable these tests once they stop flakily killing
      # the GCE instance they run on.
      # _run_tests(api, update_result, source_dir, False)
    except Exception:
      status = common_pb2.FAILURE

    summary_markdown = 'Build Succeeded.'
    if status != common_pb2.SUCCESS:
      summary_markdown = 'Build Failed.'

    dashboard_link = 'http://go/chrome-agentic-evals-dash'
    if api.tryserver.is_tryserver:
      change = api.buildbucket.build.input.gerrit_changes[0]
      # Open the dashboard to the CL view e.g.:
      # go/chrome-agentic-evals-dash/?av=m64wtezy%3Ab0yp07j7&p=Change:7103486%2F38
      dashboard_link += f'?av=m64wtezy%3Ab0yp07j7&p=Change:{change.change}%2F{change.patchset}'
    summary_markdown += (f'\nSee the [dashboard results]({dashboard_link}) for '
                         'more info (internal only).')
    return result_pb2.RawResult(
        summary_markdown=summary_markdown,
        status=status,
    )


def checkout(api: DEPS):
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


def _ensure_btrfs_subvolume(api: DEPS, path):
  with api.step.nest('ensure btrfs subvolume', status='last'):
    if not _is_btrfs_subvolume(api, path):
      api.file.rmtree('remove non-btrfs checkout dir', path)
      api.step('create btrfs subvolume', ['btrfs', 'subvolume', 'create', path])


def _is_btrfs_subvolume(api: DEPS, path):
  return api.step(
      'check btrfs subvolume',
      ['sudo', 'btrfs', 'subvolume', 'show', path],
      raise_on_failure=False,
  ).retcode == 0


def _run_tests(api: DEPS, update_result, source_dir, stable):
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
        '--enable-perf-uploading',
        '--git-revision',
        (update_result.properties.get('got_revision') or
         update_result.properties.get('got_src_revision')),
        '--gcs-bucket',
        'germanium-evals',
        '--build-id',
        build.id,
        '--builder',
        build.builder.builder,
        '--builder-group',
        api.builder_group.for_current,
        '--build-number',
        build.number,
    ])
  # TODO(b/503830579): Re-add the conditional step_name, etc. once unstable
  # evals are run again.
  # if stable:
  step_name = 'run stable eval prompts'
  cmd += ['--tag-filter=stable']
  ok_ret = (0,)
  # else:
  #   step_name = 'run unstable eval prompts'
  #   cmd += ['--tag-filter=-stable']
  #   ok_ret = 'any'
  api.step(
      step_name,
      api.resultdb.wrap(
          cmd,
          module_name='//agents/testing:eval_prompts',
          module_scheme='flat',
      ),
      ok_ret=ok_ret,
  )


def _ensure_nodejs(api: DEPS, cipd_root):
  ef = api.cipd.EnsureFile()
  ef.add_package(
      'infra/3pp/tools/nodejs/linux-${arch}',
      NODEJS_CIPD_TAG,
  )
  api.cipd.ensure(cipd_root, ef, 'ensure nodejs')


def _install_gemini_cli(api: DEPS):
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
      # TODO(b/503830579): Re-add the unstable test asserts when they are
      # re-enabled.
      # api.post_process(post_process.MustRun, 'run unstable eval prompts'),
      api.post_process(post_process.StepCommandContains,
                       'run stable eval prompts', ['--enable-perf-uploading']),
      api.post_process(post_process.StepCommandContains,
                       'run stable eval prompts', ['fake-builder']),
      api.post_process(post_process.StepCommandContains,
                       'run stable eval prompts', ['fake-group']),
      # api.post_process(post_process.StepCommandContains,
      #                  'run unstable eval prompts',
      #                  ['--enable-perf-uploading']),
      # api.post_process(post_process.StepCommandContains,
      #                  'run unstable eval prompts', ['fake-builder']),
      # api.post_process(post_process.StepCommandContains,
      #                  'run unstable eval prompts', ['fake-group']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_try',
      gen_try_test_props(),
      api.post_process(post_process.MustRun, 'run stable eval prompts'),
      # TODO(b/503830579): Re-add the unstable test asserts when they are
      # re-enabled.
      # api.post_process(post_process.MustRun, 'run unstable eval prompts'),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'run stable eval prompts', ['--enable-perf-uploading']),
      # api.post_process(post_process.StepCommandDoesNotContain,
      #                  'run unstable eval prompts',
      #                  ['--enable-perf-uploading']),
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
      # TODO(b/503830579): Re-add the unstable test asserts when they are
      # re-enabled.
      # api.step_data('run unstable eval prompts', retcode=1),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'not-a-btrfs-subvolume',
      gen_ci_test_props(),
      api.step_data('ensure btrfs subvolume.check btrfs subvolume', retcode=1),
      api.post_process(post_process.DropExpectation),
  )
