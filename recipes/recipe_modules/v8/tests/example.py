# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/properties',
    'recipe_engine/runtime',
    'v8',
    'v8_tests',
]

def RunSteps(api):
  api.v8.apply_bot_config(
      {'triggers': ['v8_triggered_bot'], 'triggers_proxy': True})
  api.v8.checkout()
  api.v8_tests.load_static_test_configs()
  compile_failure = api.v8.compile(
      test_spec=api.v8_tests.TEST_SPEC.from_python_literal(
          {'TestBuilder': {'tests': [{'name': 'v8testing'}]}},
          ['TestBuilder'],
      ),
      out_dir=api.properties.get('out_dir'),
  )
  if compile_failure:
    return compile_failure

  api.v8.maybe_trigger()


def GenTests(api):
  yield (api.v8.test('client.v8', 'V8 Foobar') +
         _job_exists(api, 'v8_triggered_bot') +
         api.v8.check_in_any_arg('compile', 'v8/out/build') +
         api.v8.check_in_any_arg('isolate tests (perf)', 'v8/out/build') +
         api.post_process(post_process.DropExpectation))

  yield (api.v8.test(
      'client.v8', 'V8 Foobar', 'custom_out_dir', out_dir='out-ref') +
         api.v8.check_in_any_arg('compile', 'v8/out-ref/build') +
         api.v8.check_in_any_arg('isolate tests (perf)', 'v8/out-ref/build') +
         api.post_process(post_process.DropExpectation))

  yield (api.v8.test('client.v8', 'V8 Foobar', 'compile_failure') +
         api.step_data('compile', retcode=1) + api.expect_status('FAILURE') +
         api.post_process(post_process.DropExpectation))


def _job_exists(api, builder_name):
  return api.step_data(
      'trigger.read jobs json',
      api.file.read_json({
          'jobs': [{
              'jobRef': {
                  'job': builder_name,
              },
          }],
      }))
