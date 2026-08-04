# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.e2e_tests_runner import E2ETests, RepeatE2EShuffledTests

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
  repeat_shuffled = api.properties.get('repeat_shuffled', False)
  if repeat_shuffled:
    runner = RepeatE2EShuffledTests(
        api, trigger, builder_config, step_name='Repeat E2E Tests')
  else:
    runner = E2ETests(api, trigger, builder_config, step_name='E2E Tests')

  if not runner.skip():
    runner.trigger()
    runner.process_results()

  if 'test_names' in api.properties:
    test_names = api.properties['test_names']
    runner.trigger_exoneration(test_names)
    runner.process_exoneration_results(test_names)

  if 'touched_tests' in api.properties:
    touched = api.properties['touched_tests']
    runner.trigger_flake_detection(touched)
    runner.process_flake_detection_results(touched)

  api.step.empty(str(runner.owns_test('test/e2e/foo.test.ts')))
  return runner.results.raw_result()


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
      'repeat_shuffled',
      try_build(),
      api.properties(
          repeat_shuffled=True,
          test_names={
              'shuffled_repeat_e2e_tests': ['test/e2e/foo.test.ts:test1']
          }),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip_debug',
      try_build(),
      api.properties(builder_config='Debug'),
      api.post_process(post_process.DoesNotRun, 'Trigger E2E Tests'),
      api.post_process(post_process.DropExpectation),
  )

  # Exoneration
  yield api.test(
      'exonerate',
      try_build(),
      api.properties(
          test_names={'e2e_tests': ['test/e2e/foo.test.ts:my_test']}),
      api.post_process(post_process.DropExpectation),
  )

  # Flake detection
  yield api.test(
      'flake_detection',
      try_build(),
      api.properties(touched_tests=['test/e2e/foo.test.ts']),
      api.post_process(post_process.DropExpectation),
  )

  # Collect with invalid shard (infra failure, test_runner_base.py line 59)
  yield api.test(
      'shard_invalid',
      try_build(),
      api.override_step_data(
          ('E2E Tests.E2E Tests shards results.'
           'E2E Tests (Shard #0) on Ubuntu-22.04'),
          api.chromium_swarming.summary(None,
                                        {'shards': [{
                                            'state': 'TIMED_OUT'
                                        }]})),
      api.post_process(post_process.DropExpectation),
      status='INFRA_FAILURE',
  )
