# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.e2e_tests_runner import E2ETests, E2ETestDivider

from RECIPE_MODULES.build.devtools.test_phases import FirstRunPhase

DEPS = [
    'builder_group',
    'chromium',
    'chromium_swarming',
    'devtools',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/git',
    'depot_tools/tryserver',
    'perf_dashboard',
    'recipe_engine/buildbucket',
    'recipe_engine/cas',
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
    'runner_args':
        Property(
            kind=str,
            help='Parameters to be passed down to the test runner for running'
            ' stress e2e tests.',
            default=None),
}


def RunSteps(api, clobber, runner_args):
  builder_config = 'Debug'
  api.devtools.configure(
      builder_config, is_official_build=False, devtools_skip_typecheck=True)
  update_result = api.devtools.update()

  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  with api.devtools.depot_on_path(source_dir):
    api.devtools.clean_out_dir(source_dir, builder_config, clobber)
    api.chromium.run_gn(source_dir, build_dir)

    compilation_result = api.chromium.compile(source_dir, build_dir)
    if compilation_result.status != common_pb.SUCCESS:
      return compilation_result
    cas_digest = api.devtools.archive_to_cas(source_dir)

    divider = E2ETestDivider(api, source_dir, builder_config)
    trigger = SwarmingTrigger(api, cas_digest)
    e2e_stressor = E2ETests(api, source_dir, trigger, builder_config, False,
                            'E2E Tests', divider)
    e2e_stressor.extra_args.extend(
        runner_args.split(' ') if runner_args else [])

    results = FirstRunPhase(api).run_all([e2e_stressor])

    return results.raw_result()

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
      status='FAILURE',
  )

  yield api.test(
      'e2e stress test',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='e2e_stressor_linux'),
      api.post_process(post_process.Filter().include_re(r'Run tests.*')),
      status='SUCCESS',
  )
