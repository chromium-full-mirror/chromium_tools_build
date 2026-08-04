# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.test_runner_base import DevToolsTests, ExonerableTests
from RECIPE_MODULES.build.devtools.unit_tests_runner import UnitTests

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
  node_mode = api.properties.get('node_mode', False)
  step_name = 'Unit Tests (node)' if node_mode else 'Unit Tests'
  runner = UnitTests(
      api,
      trigger,
      builder_config,
      coverage=True,
      step_name=step_name,
      node_unit_tests=node_mode)

  if api.properties.get('initial_failure'):
    runner.results.task_failures = [f'Failure in {step_name} (shard #0)']

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

  api.step.empty(str(runner.owns_test('front_end/foo.test.ts')))
  ExonerableTests.test_patterns.fget(runner)
  ExonerableTests.owns_test(runner, 'front_end/foo.test.ts')
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

  yield api.test(
      'basic',
      try_build(builder='dtf_linux_rel'),
  )
  yield api.test('node_mode', try_build(), api.properties(node_mode=True))

  # Exoneration with initial task failure and use_new_format=True
  yield api.test(
      'exonerate_success',
      try_build(),
      api.properties(
          initial_failure=True,
          test_names={'unit_tests': ['front_end/foo.test.ts:test1']}),
      api.post_process(post_process.DropExpectation),
  )

  # Exoneration in node_mode (had_node_unit_tests)
  yield api.test(
      'exonerate_node',
      try_build(),
      api.properties(
          node_mode=True,
          initial_failure=True,
          test_names={'node_unit_tests': ['front_end/foo.test.ts:test1']}),
      api.post_process(post_process.DropExpectation),
  )

  # Exoneration with empty test list (no owned tests)
  yield api.test(
      'exonerate_empty',
      try_build(),
      api.properties(test_names={'other_tag': ['test1']}),
      api.post_process(
          post_process.DoesNotRun,
          'Trigger Unit Tests (rerun).[trigger] Unit Tests (rerun) (Shard #0) on Ubuntu-22.04'
      ),
      api.post_process(post_process.DropExpectation),
  )

  # Exoneration with too many failures (>20)
  yield api.test(
      'exonerate_too_many',
      try_build(),
      api.properties(
          test_names={'unit_tests': [f'test_{i}' for i in range(25)]}),
      api.post_process(post_process.MustRun,
                       'Too many tests to check for flakes Unit Tests'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  # Flake detection in node_mode
  yield api.test(
      'flake_detection_node',
      try_build(),
      api.properties(node_mode=True, touched_tests=['front_end/foo.test.ts']),
      api.post_process(post_process.DropExpectation),
  )

  # Flake detection
  yield api.test(
      'flake_detection',
      try_build(),
      api.properties(touched_tests=['front_end/foo.test.ts']),
      api.post_process(post_process.DropExpectation),
  )

  # StepFailure in _post_collect coverage
  yield api.test(
      'post_collect_step_failure',
      try_build(builder='dtf_linux_rel'),
      api.step_data(
          'Unit Tests.remove coverage files if they exist', retcode=1),
      api.post_process(post_process.DropExpectation),
      status='INFRA_FAILURE',
  )

  # [UNEXPECTED BEHAVIOR / BUG]: Bug 3 - test_name_to_grep_string replaces slashes `/` with `.`
  def check_grep_pattern(check, steps, step_name, expected_grep):
    check(expected_grep in str(steps[step_name].cmd))

  yield api.test(
      'bug_test_name_to_grep_string_wildcard',
      try_build(),
      api.properties(
          test_names={'unit_tests': ['front_end/foo/bar.test.ts: suite test']}),
      api.post_process(
          check_grep_pattern,
          'Trigger Unit Tests (rerun).[trigger] Unit Tests (rerun) (Shard #0) on Ubuntu-22.04',
          'front_end.foo.bar'),
      api.post_process(post_process.DropExpectation),
  )

  def check_flake_detection_args(check, steps):
    step = steps[
        'Trigger Unit Tests (node) (flake detection).[trigger] Unit Tests (node) (flake detection) (Shard #0) on Ubuntu-22.04']
    cmd_str = ' '.join(str(x) for x in step.cmd)
    check('--node-unit-tests' in cmd_str)
    check('--repeat=10' in cmd_str)

  # Verifies that flake detection appends to extra_args without dropping existing arguments.
  yield api.test(
      'flake_detection_preserves_extra_args',
      try_build(),
      api.properties(node_mode=True, touched_tests=['front_end/foo.test.ts']),
      api.post_process(check_flake_detection_args),
      api.post_process(post_process.DropExpectation),
  )

  # [UNEXPECTED BEHAVIOR / BUG]: Bug in UnitTests coverage overwrite
  yield api.test(
      'bug_exoneration_overwrites_coverage',
      try_build(),
      api.properties(test_names={'unit_tests': ['front_end/foo.test.ts:test']}),
      api.post_process(
          post_process.MustRun,
          'Unit Tests (rerun).remove coverage files if they exist'),
      api.post_process(post_process.DropExpectation),
  )
