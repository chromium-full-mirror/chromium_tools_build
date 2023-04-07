# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium_swarming',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/swarming',
]

from recipe_engine import recipe_test_api, post_process


def RunSteps(api):
  opt_dims = api.properties.get('optional_dimensions')
  cas_input_root = api.properties.get('cas_input_root')
  task = api.chromium_swarming.task(
      name=api.properties.get('task_name', 'sample_task'),
      cas_input_root=cas_input_root,
      optional_dimensions=opt_dims,
      env_prefixes={'FOO': ['some/path']})
  if api.properties.get('wait_for_capacity'):
    task.wait_for_capacity = True

  target_os = 'Linux'
  task_slice = task.request[0]
  task_dimensions = task_slice.dimensions
  task_dimensions['os'] = target_os
  task_dimensions['pool'] = api.properties.get('pool', 'chromium.tests')
  task_slice = task_slice.with_dimensions(**task_dimensions)
  task.named_caches = dict(api.properties.get('named_caches', {}))
  task.request = task.request.with_slice(0, task_slice)

  if api.properties.get('containment_type'):
    task.containment_type = api.properties['containment_type']
  api.chromium_swarming.trigger_task(task)
  kwargs = {}
  api.chromium_swarming.collect_task(task, **kwargs)


def GenTests(api):

  yield api.test(
      'wait_for_capacity',
      api.properties(
          task_name='capacity-constrained task', wait_for_capacity=True),
      api.post_check(api.swarming.check_triggered_request,
                     '[trigger] capacity-constrained task', lambda check, req:
                     check(req[0].wait_for_capacity)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'containment_type',
      api.properties(task_name='windows gpu task', containment_type='AUTO'),
      api.post_check(
          api.swarming.check_triggered_request, '[trigger] windows gpu task',
          lambda check, req: check(req[0].containment_type == 'AUTO')),
      api.post_process(post_process.DropExpectation),
  )

  empty_digest = (
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/0')
  yield api.test(
      'cas_input_root',
      api.properties(
          task_name='task with cas input', cas_input_root=empty_digest),
      api.post_process(
          api.swarming.check_triggered_request, '[trigger] task with cas input',
          lambda check, req: check(req[0].cas_input_root == empty_digest)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'named_caches',
      api.properties(
          task_name='windows gpu task', named_caches={
              'foo': 'cache/foo',
          }),
      api.post_check(
          api.swarming.check_triggered_request, '[trigger] windows gpu task',
          lambda check, req: check(req[0].named_caches['foo'] == 'cache/foo')),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'optional_dimensions',
      api.properties(
          task_name='optional-dimension task',
          wait_for_capacity=True,
          optional_dimensions={
              60: {
                  'os': 'most-preferred-os'
              },
              120: {
                  'os': 'less-preferred-os'
              },
              180: {
                  'os': 'least-preferred-os'
              },
          }),
      api.post_check(api.swarming.check_triggered_request,
                     '[trigger] optional-dimension task', lambda check, req:
                     check(len(req) == 4)),
      api.post_process(post_process.DropExpectation),
  )

  def bad_summary_json():
    step_test_data = recipe_test_api.StepTestData()
    key = ('chromium_swarming', 'summary', None)
    placeholder = recipe_test_api.PlaceholderTestData('bad JSON')
    step_test_data.placeholder_data[key] = placeholder
    return step_test_data

  yield api.test(
      'bad-summary',
      api.properties(task_name='task'),
      api.step_data('task', bad_summary_json()),
      api.post_check(post_process.StepException, 'task'),
      api.post_check(post_process.StepTextContains, 'task',
                     ['Missing or invalid summary']),
      api.post_process(post_process.DropExpectation),
      api.expect_status('INFRA_FAILURE'),
  )
