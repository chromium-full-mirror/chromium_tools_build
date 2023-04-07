# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from recipe_engine import post_process
from recipe_engine.recipe_api import Property

import json

DEPS = [
    'builder_group',
    'chromium',
    'devtools',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/git',
    'depot_tools/tryserver',
    'perf_dashboard',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
]

PROPERTIES = {
    'clobber':
        Property(
            kind=bool,
            help='Should the builder clean up the out/ folder before building',
            default=False),
    'e2e_env':
        Property(
            kind=dict,
            help='A set of environment variables to be used when running e2e'
            'stress tests. '
            'Example vars:  the subset of tests to be run, number of '
            'iterations, etc.',
            default=None),
    # TODO: remove e2e_env property once the runner gets updated
    'runner_args':
        Property(
            kind=str,
            help='Parameters to be passed down to the test runner for running'
            ' stress e2e tests.',
            default=None),
}


def RunSteps(api, clobber, e2e_env, runner_args):
  builder_config = 'Debug'
  api.devtools.configure(
      builder_config, is_official_build=False, devtools_skip_typecheck=True)
  api.devtools.update()

  with api.devtools.depot_on_path():
    api.devtools.clean_out_dir(builder_config, clobber)
    api.chromium.run_gn()
    compilation_result = api.chromium.compile()
    if compilation_result.status != common_pb.SUCCESS:
      return compilation_result

    with api.context(env=e2e_env):
      args = runner_args.split() if runner_args else []

      if api.devtools.is_parallel_run():
        api.devtools.run_e2e(builder_config, args, 'parallel')
        api.devtools.run_e2e(builder_config, args, 'sequential')
      else:
        api.devtools.run_e2e(builder_config, args)


def GenTests(api):
  git_repo = 'https://chromium.googlesource.com/devtools/devtools-frontend'

  def ci_build(builder):
    return api.buildbucket.ci_build(
        project='devtools', builder=builder, git_repo=git_repo)

  def try_build(builder, **kwargs):
    return api.buildbucket.try_build(
        project='devtools',
        builder=builder,
        git_repo=git_repo,
        change_number=91827,
        patch_set=1,
        **kwargs)

  yield api.test(
      'compile failure',
      api.builder_group.for_current('devtools-frontend'),
      ci_build(builder='linux'),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'e2e stress test',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='e2e_stressor_linux'),
      api.properties(e2e_env={
          'ITERATIONS': '100',
          'SUITE': 'flaky suite'
      }),
      api.post_process(post_process.DoesNotRun, 'Unit Tests'),
      api.post_process(post_process.Filter('E2E tests')),
  )

  yield api.test(
      'e2e stress test with parameters',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='e2e_stressor_linux'),
      api.properties(runner_args="--ITERATIONS=100 --SUITE=flaky/suite"),
      api.post_process(post_process.DoesNotRun, 'Unit Tests'),
      api.post_process(post_process.Filter('E2E tests')),
  )

  yield api.test(
      'parallel stress builder',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_stressor_linux'),
      api.post_process(post_process.MustRun, 'E2E tests (Parallel)'),
      api.post_process(post_process.MustRun, 'E2E tests (Sequential)'),
      api.post_process(post_process.DropExpectation))
