# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from RECIPE_MODULES.build.devtools.test_phases import TaskCoordinator

DEPS = [
    'chromium_swarming',
    'devtools',
    'recipe_engine/futures',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/step',
]


class MockTask:

  def __init__(self, task_ids):
    self._task_ids = list(task_ids)

  def get_task_ids(self):
    return self._task_ids


def RunSteps(api):
  # Edge Case 1: Multi-phase runners and multi-iteration polling
  if api.properties.get('test_multiphase', False):
    coord = TaskCoordinator(api)
    events = []

    def multi_phase_runner():
      events.append('r1_start')
      coord.register_and_wait([MockTask(['r1-init-1', 'r1-init-2'])])
      events.append('r1_init_done')
      coord.register_and_wait([MockTask(['r1-flake'])])
      events.append('r1_flake_done')

    def other_runner():
      events.append('r2_start')
      coord.register_and_wait([MockTask(['r2-task'])])
      events.append('r2_done')

    f1 = api.futures.spawn(multi_phase_runner)
    f2 = api.futures.spawn(other_runner)
    coord.run_poller([f1, f2])

    assert set(events) == {
        'r1_start', 'r2_start', 'r1_init_done', 'r1_flake_done', 'r2_done'
    }, events
    return

  # Edge Case 2: Crashed worker aborts pending waiting worker
  if api.properties.get('test_crash_with_pending', False):
    coord = TaskCoordinator(api)
    unblocked = []

    def waiting_worker():
      coord.register_and_wait([MockTask(['task-pending'])])
      unblocked.append('worker_unblocked')

    def crashing_worker():
      raise RuntimeError('simulated bot crash')

    f_wait = api.futures.spawn(waiting_worker)
    # Yield to let waiting_worker register
    api.futures.wait([f_wait], timeout=0.01, count=1)
    f_crash = api.futures.spawn(crashing_worker)

    try:
      coord.run_poller([f_wait, f_crash])
      assert False, 'Expected RuntimeError'  # pragma: no cover
    except RuntimeError as e:
      assert str(e) == 'simulated bot crash', str(e)

    # Verify waiting worker was unblocked by abort
    f_wait.result()
    assert unblocked == ['worker_unblocked'], unblocked
    return

  # Edge Case 3: Empty tasks / MockTask with empty IDs -> return immediately
  coord = TaskCoordinator(api)
  coord.register_and_wait([])
  coord.register_and_wait([MockTask([])])
  assert len(coord._pending) == 0, (  # pylint: disable=protected-access
      f'Expected 0 pending, got {len(coord._pending)}')

  # Edge Case 4: Zero futures in poller -> terminates immediately
  coord.run_poller([])

  # Edge Case 5: Multiple aborts are safe and idempotent
  coord.abort()
  coord.abort()

  # Edge Case 6: Fast runner that never registers tasks + multiple runners
  # with multiple task objects + unknown extraneous task returned in sets
  completed = []

  def no_task_runner():
    completed.append('no_task_runner')

  def multi_task_runner(tasks, name):
    coord.register_and_wait(tasks)
    completed.append(name)

  f_fast = api.futures.spawn(no_task_runner)
  f1 = api.futures.spawn(
      multi_task_runner,
      [MockTask(['task-1']), MockTask(['task-2'])],
      'runner1',
  )
  f2 = api.futures.spawn(
      multi_task_runner,
      [MockTask(['task-3'])],
      'runner2',
  )

  coord.run_poller([f_fast, f1, f2])

  assert 'no_task_runner' in completed, completed
  assert 'runner1' in completed, completed
  assert 'runner2' in completed, completed
  assert len(coord._pending) == 0, (  # pylint: disable=protected-access
      coord._pending)

  api.step.empty('TaskCoordinator unit tests passed')


def GenTests(api):
  yield api.test(
      'basic',
      api.step_data(
          'wait for tasks',
          api.json.output({
              'attempts': 0,
              # Reversed order + unknown untracked task set
              'sets': [['task-2', 'task-1'], ['task-3'], ['untracked-task']],
          }),
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'staggered_multiphase',
      api.properties(test_multiphase=True),
      api.step_data(
          'wait for tasks',
          api.json.output({
              'attempts': 1,
              'sets': [],
          }),
      ),
      api.step_data(
          'wait for tasks (2)',
          api.json.output({
              'attempts': 2,
              'sets': [['r1-init-2', 'r1-init-1']],
          }),
      ),
      api.step_data(
          'wait for tasks (3)',
          api.json.output({
              'attempts': 0,
              'sets': [['r2-task'], ['r1-flake']],
          }),
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'crash_aborts_pending',
      api.properties(test_crash_with_pending=True),
      api.post_process(post_process.DropExpectation),
  )
