# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import (DoesNotRun, DropExpectation,
                                        LogContains, MustRun)

DEPS = [
    'builder_group',
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
  update_result = api.v8.checkout()
  source_dir = update_result.source_root.path
  build_dir = api.v8.build_dir(source_dir)
  api.v8_tests.load_static_test_configs()
  compile_failure = api.v8.compile(
      source_dir,
      build_dir,
      test_spec=api.v8_tests.TEST_SPEC.from_python_literal(
          {'TestBuilder': {
              'tests': [{
                  'name': 'v8testing'
              }]
          }},
          ['TestBuilder'],
      ),
  )
  if compile_failure:
    return compile_failure

  api.v8.maybe_trigger()


def GenTests(api):
  yield (api.v8.test('client.v8', 'V8 Foobar') +
         _job_exists(api, 'v8_triggered_bot') +
         api.v8.check_in_any_arg('compile', 'v8/out/build') +
         api.v8.check_in_any_arg('isolate tests (perf)', 'v8/out/build') +
         api.post_process(DropExpectation))

  yield (api.v8.test('client.v8', 'V8 Foobar', 'compile_failure') +
         api.step_data('compile', retcode=1) + api.expect_status('FAILURE') +
         api.post_process(DoesNotRun, 'isolate tests') +
         api.post_process(DropExpectation))

  always_isolate_properties = {
      '$build/v8': {
          'always_isolate_targets': ['x19']
      },
  }
  yield (api.v8.test('client.v8', 'V8 Foobar', 'always_isolate') +
         api.step_data('compile', retcode=1) + api.expect_status('FAILURE') +
         api.properties(**always_isolate_properties) +
         api.post_process(MustRun, 'isolate tests') + api.post_process(
             LogContains, 'generate_build_files', 'swarming-targets-file.txt',
             ['bot_default', 'perf', 'x19']) +
         api.post_process(DropExpectation))

  unauthorized_build_msg = api.buildbucket.try_build_message(
      project='v8',
      revision='deadbeef' * 5,
      builder='v8_foobar_rel',
      git_repo='https://chromium.googlesource.com/v8/v8',
      change_number=456789,
      patch_set=12,
  )
  api.buildbucket.update_backend_service_account(
      unauthorized_build_msg, 'malicious-account@google.com')

  yield (api.test('unauthorized_service_account') +
         api.builder_group.for_current('tryserver.v8') +
         api.buildbucket.build(unauthorized_build_msg) +
         api.expect_status('INFRA_FAILURE') + api.post_process(DropExpectation))


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
