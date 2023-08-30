# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from google.protobuf import timestamp_pb2
from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2

DEPS = [
    'chromium',
    'chromium_rts',
    'recipe_engine/buildbucket',
    'recipe_engine/cq',
    'recipe_engine/file',
    'recipe_engine/json',
]


def RunSteps(api):
  api.chromium_rts.get_reuseable_compilator_build('builder-compilator')
  if api.chromium_rts.inverted_rts:
    api.chromium_rts.download_previous_code_coverage()


def GenTests(api):

  def _create_quick_run_build(include_coverage=True):
    reuseable_qr = build_pb2.Build(
        id=1234,
        status='SUCCESS',
        create_time=timestamp_pb2.Timestamp(seconds=1598338800),
        output=build_pb2.Build.Output())
    reuseable_qr.output.properties['rts_was_used'] = True
    if include_coverage:
      reuseable_qr.output.properties[
          'coverage_gs_bucket'] = "code-coverage-data"
      reuseable_qr.output.properties['merged_profdata_gs_paths'] = [
          "presubmit/chromium-review.googlesource.com/111111/1/try/fake-orchestrator/123456789/merged.profdata",
          "presubmit/chromium-review.googlesource.com/111111/1/try/fake-orchestrator_unit/123456789/merged.profdata"
      ]
    return reuseable_qr

  yield api.test(
      'basic',
      api.chromium.try_build(
          builder='fake-orchestrator',
          experiments=['chromium_rts.inverted_rts'],
          tags=api.buildbucket.tags(cq_equivalent_cl_group_key='12345')),
      api.cq(run_mode='FULL_RUN'),
      api.buildbucket.simulated_search_results(
          [_create_quick_run_build()], step_name='find successful Quick Runs'),
      api.chromium_rts.override_reused_compilator_steps(
          tests=['browser_tests', 'content_unittests']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'inverse_fyi',
      api.chromium.try_build(
          builder='builder-inverse-fyi',
          experiments=['chromium_rts.inverted_rts'],
          tags=api.buildbucket.tags(cq_equivalent_cl_group_key='12345')),
      api.cq(run_mode='FULL_RUN'),
      api.buildbucket.simulated_search_results(
          [_create_quick_run_build()], step_name='find successful Quick Runs'),
      api.chromium_rts.override_reused_compilator_steps(
          tests=['browser_tests', 'content_unittests']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_compilator_in_build',
      api.chromium.try_build(
          builder='fake-orchestrator',
          experiments=['chromium_rts.inverted_rts'],
          tags=api.buildbucket.tags(cq_equivalent_cl_group_key='12345')),
      api.cq(run_mode='FULL_RUN'),
      api.buildbucket.simulated_search_results(
          [_create_quick_run_build()], step_name='find successful Quick Runs'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_reusable_coverage',
      api.chromium.try_build(
          experiments=['chromium_rts.inverted_rts'],
          tags=api.buildbucket.tags(cq_equivalent_cl_group_key='12345')),
      api.cq(run_mode='FULL_RUN'),
      api.buildbucket.simulated_search_results(
          [_create_quick_run_build(include_coverage=False)],
          step_name='find successful Quick Runs'),
      api.chromium_rts.override_reused_compilator_steps(
          tests=['browser_tests', 'content_unittests']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'footer_disabled',
      api.chromium.try_build(
          experiments=['chromium_rts.inverted_rts'],
          tags=api.buildbucket.tags(cq_equivalent_cl_group_key='12345')),
      api.cq(run_mode='FULL_RUN'),
      api.step_data('parse description',
                    api.json.output({'Disable-Rts': ['true']})),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'bail_early_experiment_bails_early',
      api.chromium.try_build(
          experiments=['chromium_rts.inverted_rts_bail_early'],
          tags=api.buildbucket.tags(cq_equivalent_cl_group_key='12345')),
      api.cq(run_mode='QUICK_DRY_RUN'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'bail_early_experiment_reuse',
      api.chromium.try_build(
          experiments=['chromium_rts.inverted_rts_bail_early'],
          tags=api.buildbucket.tags(cq_equivalent_cl_group_key='12345')),
      api.cq(run_mode='DRY_RUN'),
      api.post_process(post_process.DropExpectation),
  )