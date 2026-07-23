# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from .commons import Results
from .test_runner_base import FLAKE_DETECTION_SKIPPED_TESTS_FOOTER


def run_test_pipelines(api, runners):
  """
  Runs the full test execution pipeline for multiple runners concurrently.
  For each runner, it triggers the initial run, waits for results, and then
  proceeds to exoneration and flake detection if applicable.
  """
  with api.step.nest('find new tests') as presentation:
    with api.context(cwd=api.devtools.source_dir):
      git_changes = api.v8.git_output('diff', '--name-only', '--format=',
                                      '--diff-filter=d',
                                      '--cached').splitlines()
      touched_tests = [
          file for file in git_changes
          if file.endswith('test.ts') or file.endswith('test.api.ts')
      ]
      presentation.logs['tests'] = touched_tests
      if api.tryserver.is_tryserver:
        skip_tests = api.tryserver.get_footer(
            FLAKE_DETECTION_SKIPPED_TESTS_FOOTER)
        touched_tests = [t for t in touched_tests if t not in skip_tests]

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

  def _run_pipeline(runner):
    with api.step.nest(f'Pipeline {runner.step_name}'):
      with api.step.nest('Run tests'):
        runner.trigger()
        runner.process_results()

      if hasattr(runner, 'trigger_exoneration'):
        failed_tests = _get_failed_tests_for_runner(runner.test_type_tag)
        if failed_tests:
          test_names = {runner.test_type_tag: failed_tests}
          with api.step.nest('Flake exoneration attempt') as presentation:
            runner.trigger_exoneration(test_names)
            runner.process_exoneration_results(test_names)
            if runner.results.task_failures:
              presentation.step_text = 'Failed to exonerate some of the failing tests'

      if hasattr(runner, 'trigger_flake_detection'):
        with api.step.nest('Detect flakes in new tests'):
          runner.trigger_flake_detection(touched_tests)
          runner.process_flake_detection_results(touched_tests)

    return runner.results

  futures = [api.futures.spawn(_run_pipeline, r) for r in runners]
  api.futures.wait(futures)
  results = Results()
  for future in futures:
    results += future.result()

  return results
