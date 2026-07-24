# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from shlex import split
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from recipe_engine import post_process
from PB.recipes.build.devtools.dtf_e2e_stress import InputProperties

from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.e2e_tests_runner import E2ETests

from RECIPE_MODULES.build.devtools.test_phases import run_test_pipelines

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
    'recipe_engine/futures',
    'v8',
]

PROPERTIES = InputProperties


def RunSteps(api, properties):
  builder_config = 'Debug'
  api.devtools.configure(
      builder_config, is_official_build=False, devtools_skip_typecheck=True)
  api.devtools.update()

  build_dir = api.devtools.source_dir / 'out' / api.chromium.c.build_config_fs
  with api.devtools.depot_on_path():
    api.devtools.clean_out_dir(builder_config, properties.clobber)
    with api.chromium.guard_compile(build_dir):
      api.chromium.run_gn(api.devtools.source_dir, build_dir)

      compilation_result = api.chromium.compile(api.devtools.source_dir,
                                                build_dir)
      if compilation_result.status != common_pb.SUCCESS:
        return compilation_result
    cas_digest = api.devtools.archive_to_cas()

    trigger = SwarmingTrigger(api, cas_digest)
    e2e_stressor = E2EStressTests(api, trigger, builder_config, 'E2E Tests',
                                  properties.runner_args)

    results = run_test_pipelines(api, [e2e_stressor])

    return results.raw_result()


class E2EStressTests(E2ETests):

  def __init__(self, api, trigger, builder_config, step_name, runner_args):
    super().__init__(api, trigger, builder_config, step_name)
    self.test_list = []
    self.extra_args = []
    for arg in split(runner_args or ""):
      is_option = arg.startswith('-')
      (self.extra_args if is_option else self.test_list).append(arg)
    self.runner_args = runner_args

  def commands(self):
    tests = self.test_list or ['test/e2e']
    return [self.run_tests_command(tests)]


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
      'e2e stress test default',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='e2e_stressor_linux'),
      api.post_process(post_process.Filter().include_re(r'.*Pipeline.*')),
      status='SUCCESS',
  )

  yield api.test(
      'e2e stress test with args',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='e2e_stressor_linux'),
      api.properties(runner_args='test123 --repeat=2'),
      api.post_process(post_process.Filter().include_re(r'.*Pipeline.*')),
      status='SUCCESS',
  )
