# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.api_tests_runner import ApiTests

DEPS = [
    'devtools',
    'chromium',
    'chromium_swarming',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/path',
    'recipe_engine/platform',
]


def RunSteps(api):
  builder_config = api.properties.get('builder_config', 'Release')
  api.devtools.configure(
      builder_config, is_official_build=False, devtools_skip_typecheck=True)
  api.devtools.update()

  trigger = SwarmingTrigger(api, '1234567/890')
  runner = ApiTests(api, trigger, builder_config, step_name='API Tests')

  if not runner.skip():
    runner.trigger()
    runner.process_results()

  if 'touched_tests' in api.properties:
    touched = api.properties['touched_tests']
    runner.trigger_flake_detection(touched)
    runner.process_flake_detection_results(touched)

  api.step.empty(str(runner.owns_test('front_end/foo.test.api.ts')))


def GenTests(api):
  git_repo = 'https://chromium.googlesource.com/devtools/devtools-frontend'

  def try_build(builder='linux'):
    return api.buildbucket.try_build(
        project='devtools',
        builder=builder,
        git_repo=git_repo,
        change_number=91827,
        patch_set=1)

  yield api.test('basic', try_build())
  yield api.test(
      'flake_detection',
      try_build(),
      api.properties(touched_tests=['front_end/foo.test.api.ts']),
      api.post_process(post_process.DropExpectation),
  )
