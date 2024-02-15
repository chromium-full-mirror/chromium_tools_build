# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe used to launch CI build with the rr tool.

The recipe will run top flaky tests using the rr tool, upload the recorded
traces to GCS.
"""

from recipe_engine.post_process import (DoesNotRun, DropExpectation,
                                        LogContains, ResultReason,
                                        StepCommandRE, StepFailure, StepSuccess)
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/gclient',
    'depot_tools/tryserver',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api):

  api.gclient.set_config('chromium_skip_wpr_archives_download')
  api.chromium_checkout.ensure_checkout()
  api.gclient.runhooks()

  api.context(cwd=api.path['checkout'])
  api.chromium.set_config('chromium')
  raw_result = api.chromium.compile(
      targets=['blink_web_tests', 'blink_wpt_tests', 'chrome_wpt_tests'],
      use_reclient=True)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  cmd = [api.path['checkout'].join('third_party/blink/tools/run_web_tests.py')]
  # Use one test for reference now.
  cmd.extend([
      '-t', 'Release', '--no-retry-failures',
      'external/wpt/css/css-transforms/z-index-does-not-apply.html'
  ])
  api.step(name='run web tests', cmd=cmd, raise_on_failure=True)


def GenTests(api):
  yield api.test(
      'happy_path_run_web_test',
      api.post_process(StepCommandRE, 'run web tests', [
          '.*/third_party/blink/tools/run_web_tests.py',
          '-t',
          'Release',
          '--no-retry-failures',
          'external/wpt/css/css-transforms/z-index-does-not-apply.html',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'run_with_failure',
      api.step_data('run web tests', retcode=1),
      api.post_process(StepFailure, 'run web tests'),
      api.expect_status('FAILURE'),
      api.post_process(ResultReason, 'Step(\'run web tests\') (retcode: 1)'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'compile_with_failure',
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )
