# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import fnmatch

from .commons import Results
from .test_runner_base import (FLAKE_DETECTION_SKIPPED_TESTS_FOOTER,
                               FLAKE_DETECTION_SKIPPED_TESTS_PATTERN_FOOTER)


class TaskCoordinator:
  """Coordinates asynchronous Swarming task polling across concurrent runners.

  Instead of runners calling `collect_task()` which synchronously blocks the
  recipe engine while waiting for remote Swarming execution, runners register
  their active Swarming tasks with the coordinator and wait on a channel.
  The coordinator polls task states via `wait_for_finished_task_set` and signals
  each runner when its tasks are finished so `collect_task()` completes
  immediately.
  """

  def __init__(self, api):
    self._api = api
    self._pending = {}  # frozenset(task_ids) -> (task_list, channel)
    self._attempts = 0

  def register_and_wait(self, tasks):
    """Called by a runner greenlet to wait until tasks complete on Swarming."""
    task_ids = sum((t.get_task_ids() for t in tasks), [])
    if not task_ids:
      return
    ch = self._api.futures.make_channel()
    self._pending[frozenset(task_ids)] = (task_ids, ch)
    ch.get()

  def abort(self):
    """Aborts all waiting channels on error."""
    pending = self._pending
    self._pending = {}
    for _, ch in pending.values():
      ch.put(None)

  def run_poller(self, futures):
    """Polls pending tasks until all runner futures are complete."""
    while not all(f.done for f in futures):
      for f in futures:
        if f.done and f.exception():
          self.abort()
          f.result()

      if self._pending:
        task_sets = [task_ids for task_ids, _ in self._pending.values()]
        finished_sets, self._attempts = (
            self._api.chromium_swarming.wait_for_finished_task_set(
                task_sets, attempts=self._attempts))
        for task_set in finished_sets:
          key = frozenset(task_set)
          item = self._pending.pop(key, None)
          if item:
            _, ch = item
            ch.put(None)
      else:
        running_futures = [f for f in futures if not f.done]
        if running_futures:
          self._api.futures.wait(running_futures, timeout=0.1, count=1)

    for f in futures:
      if f.exception():
        self.abort()
        f.result()


def run_test_pipelines(api, runners, affected_files=None):
  """
  Runs the full test execution pipeline for multiple runners concurrently.
  For each runner, it triggers the initial run, waits for results, and then
  proceeds to exoneration and flake detection if applicable.
  """
  with api.step.nest('find new tests') as presentation:
    if affected_files is None:
      affected_files = api.devtools.get_affected_files()
    touched_tests = [
        file for file in affected_files
        if file.endswith('test.ts') or file.endswith('test.api.ts')
    ]
    presentation.logs['tests'] = touched_tests
    if api.tryserver.is_tryserver:
      skip_tests = api.tryserver.get_footer(
          FLAKE_DETECTION_SKIPPED_TESTS_FOOTER)
      skip_patterns = api.tryserver.get_footer(
          FLAKE_DETECTION_SKIPPED_TESTS_PATTERN_FOOTER)
      touched_tests = [t for t in touched_tests if t not in skip_tests]
      for pattern in skip_patterns:
        touched_tests = [
            t for t in touched_tests if not fnmatch.fnmatch(t, pattern)
        ]
    unowned_tests = [
        t for t in touched_tests
        if not any(r.owns_test(t) for r in runners if hasattr(r, 'owns_test'))
    ]
    if unowned_tests:
      presentation.logs['unowned tests'] = unowned_tests
      presentation.status = api.step.WARNING
      presentation.step_text = (
          'The following touched tests are not owned by any runner: ' +
          ', '.join(unowned_tests))

  def _get_failed_tests_for_runner(test_type_tag):
    inv_id = api.resultdb.current_invocation.replace('invocations/', '')
    response = api.resultdb.query(
        inv_ids=[inv_id],
        tr_fields=['testId', 'tags', 'expected'],
        limit=0,
        step_name=f'rdb query for {test_type_tag}')
    proto_results = sum((res.test_results for res in response.values()), [])

    passing_tests = set()
    failing_tests = set()
    for r in proto_results:
      tag = next((t.value for t in r.tags if t.key == 'test_type'), None)
      if tag == test_type_tag:
        if r.expected:
          passing_tests.add(r.test_id)
        else:
          failing_tests.add(r.test_id)

    return list(failing_tests - passing_tests)

  coordinator = TaskCoordinator(api)

  def _run_pipeline(runner):
    with api.step.nest(f'Pipeline {runner.step_name}'):
      with api.step.nest('Run tests'):
        runner.trigger()
        runner.process_results(coordinator)

      failed_tests = _get_failed_tests_for_runner(runner.test_type_tag)
      if failed_tests:
        with api.step.nest('test re-run cmd') as presentation:
          presentation.step_text = 'npm run test -- ' + ' '.join(failed_tests)

      if hasattr(runner, 'trigger_exoneration'):
        if failed_tests:
          test_names = {runner.test_type_tag: failed_tests}
          with api.step.nest('Flake exoneration attempt') as presentation:
            presentation.logs['found tests'] = failed_tests
            runner.trigger_exoneration(test_names)
            runner.process_exoneration_results(test_names, coordinator)
            if runner.results.task_failures:
              presentation.step_text = (
                  'Failed to exonerate some of the failing tests')

      if hasattr(runner, 'trigger_flake_detection'):
        with api.step.nest('Detect flakes in new tests'):
          runner.trigger_flake_detection(touched_tests)
          runner.process_flake_detection_results(touched_tests, coordinator)

    return runner.results

  futures = [api.futures.spawn(_run_pipeline, r) for r in runners]
  coordinator.run_poller(futures)
  results = Results()
  for future in futures:
    results += future.result()

  return results
