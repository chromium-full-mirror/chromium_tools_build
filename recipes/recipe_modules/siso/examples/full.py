# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from PB.recipe_modules.recipe_engine.led.properties import InputProperties

DEPS = [
    'recipe_engine/properties',
    'siso',
]


def RunSteps(api):
  env = {}
  if api.siso.enabled:
    api.siso.run_ninja(
        ninja_command=api.properties.get('build_command'),
        ninja_env=env,
        name=api.properties.get('name'),
        siso_args=api.properties.get('siso_args'),
    )


def GenTests(api):
  yield api.test(
      'basic',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(),
  )
  yield api.test(
      'siso_args',
      api.properties(
          build_command=['ninja', '-C', 'out/Release'], siso_args=['-foo']),
      api.siso.properties(),
  )
  yield api.test(
      'reapi_address',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(
          reapi_address='us-east1-remotebuildexecution.googleapis.com:443'),
      api.post_process(post_process.StepCommandContains, 'compile', [
          '--reapi_address', 'us-east1-remotebuildexecution.googleapis.com:443'
      ]),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'cloud_trace',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(enable_cloud_trace=True),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--enable_cloud_trace']),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'cloud_profiler',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(enable_cloud_profiler=True),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--enable_cloud_profiler']),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'action_salt',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(action_salt='xxx'),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--action_salt', 'xxx']),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'sisoexperiments',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(experiments=['no-file-access-trace']),
  )
  yield api.test(
      'compile_failure',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(action_salt='xxx'),
      api.step_data('compile', retcode=1),
      api.post_process(post_process.StepFailure, 'compile'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'compile_failure_unexpected_retcode',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(action_salt='xxx'),
      api.step_data('compile', retcode=255),
      api.post_process(post_process.StepFailure, 'compile'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('INFRA_FAILURE'),
  )
  yield api.test(
      'ninja_no_C',
      api.properties(build_command=['ninja']),
      api.siso.properties(),
  )
