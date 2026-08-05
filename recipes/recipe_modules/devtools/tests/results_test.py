# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.recipe_api import StepFailure, InfraFailure
from RECIPE_MODULES.build.devtools.commons import Results

DEPS = [
    'devtools',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api):
  # 1. Basic addition of Results objects and explicit add methods
  r1 = Results()
  r1.add_infra_failure('infra1')
  r1.add_test_failure('task1')
  r2 = Results(exonerated_failures=['exon1'])
  combined = r1 + r2
  assert combined.infra_failures == ['infra1'], combined.infra_failures
  assert combined.task_failures == ['task1'], combined.task_failures
  assert combined.exonerated_failures == ['exon1'], combined.exonerated_failures

  # 2. Test exoneration: task_failures are only cleared when individual tests
  # were retried (exoneration_tests is non-empty).
  def simulate_exoneration(initial, rerun, exoneration_tests):
    if exoneration_tests and rerun.can_exonerate():
      rerun.exonerated_failures = list(exoneration_tests)
      initial.task_failures = []
    return initial + rerun

  initial_results = Results(
      task_failures=['Failure in Shard 0 (crash)', 'Failure in Shard 1 (test)'])
  rerun_results = Results()
  final_results = simulate_exoneration(
      initial_results, rerun_results, exoneration_tests=[])
  assert final_results.task_failures == [
      'Failure in Shard 0 (crash)', 'Failure in Shard 1 (test)'
  ], final_results.task_failures
  assert (final_results.exonerated_failures == []
         ), final_results.exonerated_failures

  initial_results2 = Results(task_failures=['Failure in Shard 1 (test)'])
  rerun_results2 = Results()
  final_results2 = simulate_exoneration(
      initial_results2,
      rerun_results2,
      exoneration_tests=['front_end/foo.test.ts:my_test'])
  assert final_results2.task_failures == [], final_results2.task_failures
  assert final_results2.exonerated_failures == [
      'front_end/foo.test.ts:my_test'
  ], final_results2.exonerated_failures

  # 3. Test raise_on_failure prioritizing test failure over infra failure
  r_test = Results(task_failures=['test1'])
  try:
    r_test.raise_on_failure()
  except StepFailure:
    pass

  r_infra = Results(infra_failures=['infra1'])
  try:
    r_infra.raise_on_failure()
  except InfraFailure:
    pass

  # 4. Test raw_result summary markdown with exonerated failures
  r_exon = Results(exonerated_failures=['test1'])
  res = r_exon.raw_result()
  assert res.summary_markdown == 'Flaky tests exonerated: test1', (
      res.summary_markdown)

  # 5. Test exonerable() returns False when any infra failure is present
  r_mixed = Results(task_failures=['test1'], infra_failures=['infra1'])
  assert not r_mixed.exonerable()
  assert not r_mixed.can_exonerate()

  api.step.empty('Results unit tests verified')


def GenTests(api):
  yield api.test('basic', api.post_process(post_process.DropExpectation))
