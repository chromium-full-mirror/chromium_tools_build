# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import (
  DropExpectation,
  LogContains,
  MustRun,
  StepCommandContains,
  StepTextEquals,
)

from RECIPE_MODULES.build.chromium_tests import steps

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  presentation_utils,
  test_utils,
)
from RECIPE_MODULES.recipe_engine import (
  assertions,
  buildbucket,
  json,
  path,
  properties,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  json: json.API
  path: path.API
  presentation_utils: presentation_utils.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  json: json.TEST_API
  raw_io: raw_io.TEST_API
  test_utils: test_utils.TEST_API


def RunSteps(api: DEPS):
  api.chromium.set_config('chromium')

  test_spec = steps.ScriptTestSpec.create(
    'script_test',
    script='script.py',
    compile_targets=['compile_target'],
    script_args=['some', 'args'],
  )
  test = test_spec.get_test(api.chromium_tests)
  api.assertions.assertEqual(test.option_flags, steps.TestOptionFlags.create())

  checkout_dir = api.path.cache_dir / 'builder'
  source_dir = checkout_dir / 'src'
  build_dir = source_dir / 'out' / 'some_build_dir'

  try:
    api.test_utils.run_tests_once(
      checkout_dir, source_dir, build_dir, [test], 'with patch'
    )

  finally:
    if test.with_patch_failures_including_retry():
      test._only_retry_failed_tests = True

      test.pre_run('without patch')
      test.run(checkout_dir, source_dir, build_dir, 'without patch')

    api.step('details', [])
    api.step.active_result.presentation.logs['details'] = [
      'compile_targets: {!r}'.format(test.compile_targets()),
      'uses_local_devices: {!r}'.format(test.uses_local_devices),
    ]


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.post_process(
      StepCommandContains,
      'script_test (with patch)',
      [
        'vpython3',
        '[CACHE]/builder/src/testing/scripts/script.py',
      ],
    ),
    api.post_process(
      StepCommandContains,
      'script_test (with patch)',
      [
        '--args',
        '["some", "args"]',
      ],
    ),
    api.post_process(
      LogContains,
      'details',
      'details',
      ["compile_targets: ('compile_target',)"],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'invalid_results',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.override_step_data('script_test (with patch)', api.json.output({})),
    api.post_process(
      MustRun, 'script_test with suffix with patch had an invalid result'
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'failure',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.override_step_data(
      'script_test (with patch)',
      api.json.output({'valid': True, 'failures': ['TestOne']}),
      api.raw_io.stream_output_text(
        (
          'rdb-stream: included "invocations/script_test" in'
          ' "invocations/build-inv"'
        ),
        'stderr',
      ),
    ),
    api.override_step_data(
      'script_test results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results('script_test', failing_tests=['TestOne'])
      ),
    ),
    api.post_process(
      StepTextEquals,
      'script_test (with patch)',
      '<br/>failures:<br/>[TestOne](https://luci-milo.appspot.com/ui/inv/build:8945511751514863184/test-results?q=TestOne)<br/>',
    ),
    api.post_process(DropExpectation),
  )
