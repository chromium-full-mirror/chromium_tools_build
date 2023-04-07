# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Orchestrator recipe.

Trigger the compilator to perform all check-out-related tasks and streams
its steps. Afterwards perform testing based on compilator properties.

The orchestrator is only used in a trybot setting.
"""

import json

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2
from PB.recipe_modules.recipe_engine.led import properties as led_properties_pb

from recipe_engine.post_process import (DoesNotRun, DropExpectation, MustRun,
                                        ResultReason)
from recipe_engine.recipe_api import Property

from google.protobuf import json_format
from google.protobuf import struct_pb2

DEPS = [
  'depot_tools/gitiles',
  'depot_tools/tryserver',
  'isolate',
  'recipe_engine/buildbucket',
  'recipe_engine/cipd',
  'recipe_engine/file',
  'recipe_engine/json',
  'recipe_engine/led',
  'recipe_engine/path',
  'recipe_engine/properties',
  'recipe_engine/runtime',
  'recipe_engine/step',
  'recipe_engine/swarming',
  'v8_orchestrator',
  'v8_tests',
]

PROPERTIES = {
    # Name of the compilator trybot to use.
    'compilator_name': Property(kind=str),
}

BUILD_CANCELED_SUMMARY = 'Build was canceled.'
BUILD_WRONGLY_CANCELED_SUMMARY = (
    'Compilator was canceled before the parent orchestrator was canceled.')


def orchestrator_steps(api, compilator_name):
  v8 = api.v8_tests
  compilator_handler = api.v8_orchestrator.create_compilator_handler()

  with api.step.nest('initialization'):
    # Start compilator build.
    build = compilator_handler.trigger_compilator(compilator_name)

    # Initialize V8 testing.
    v8.set_config('v8')
    v8.read_cl_footer_flags()
    v8.load_static_test_configs()

    api.swarming.ensure_client()
    v8.set_up_swarming()

  # Wait for compilator build to complete and stream steps.
  sub_build = compilator_handler.launch_compilator_watcher(build)

  # This condition should be rare as swarming only propagates
  # cancelations from parent -> child.
  if sub_build.status == common_pb.CANCELED:
    if api.runtime.in_global_shutdown:
      return result_pb2.RawResult(
          status=common_pb.CANCELED, summary_markdown=BUILD_CANCELED_SUMMARY)
    raise api.step.InfraFailure(BUILD_WRONGLY_CANCELED_SUMMARY)

  if 'compilator_properties' not in sub_build.output.properties:
    return result_pb2.RawResult(
        status=sub_build.status, summary_markdown=sub_build.summary_markdown)

  # Initialize the test specs the compilator retrieved from the checkout.
  comp_props = sub_build.output.properties['compilator_properties']
  tests = v8.extra_tests_from_properties(
      {'parent_test_spec': dict(comp_props['parent_test_spec'])})

  if not tests:
    # Running a testing trybot that doesn't specify tests might point to
    # a configuration error.
    return result_pb2.RawResult(
        status=common_pb.FAILURE, summary_markdown='No tests specified')

  # Run tests and initialize other configs needed for testing. GN args
  # from compilation to show on test failures and CAS digests of isolated
  # build artifacts to set up swarming tasks.
  v8.gn_args = list(comp_props['gn_args'])
  v8.isolated_tests = dict(comp_props['swarm_hashes'])
  test_results = v8.runtests(tests)

  if test_results.has_failures:
    # Let tryjobs fail for failures only.
    raise api.step.StepFailure('Failures in tryjob.')


def RunSteps(api, compilator_name):
  try:
    return orchestrator_steps(api, compilator_name)
  finally:
    if api.runtime.in_global_shutdown:
      # pylint: disable=lost-exception
      # Cancellation can cause all sorts of spurious exceptions.
      return result_pb2.RawResult(
          status=common_pb.CANCELED,
          summary_markdown=BUILD_CANCELED_SUMMARY)

def GenTests(api):
  def subbuild_data(
      output_properties, summary='', status=common_pb.SUCCESS):
    output_properties = output_properties or {}
    sub_build = build_pb2.Build(
        id=54321,
        status=status,
        summary_markdown=summary,
        output=dict(
            properties=json_format.Parse(
                api.json.dumps(output_properties), struct_pb2.Struct())))
    return api.step_data('compilator steps', api.step.sub_build(sub_build))

  # Minimal v8-side test spec for simulating most recipe features.
  test_spec = json.dumps({
    'swarming_task_attrs': {
      'priority': 25,
      'expiration': 1200,
      'hard_timeout': 3600,
    },
    "tests": [
      {"name": "v8testing"},
      {"name": "test262", "test_args": ["--extra-flags=--flag"]},
    ],
  }, indent=2)
  parent_test_spec = api.v8_tests.example_parent_test_spec_properties(
      'v8_foobar_rel', test_spec)

  # Dummy CAS digest hashes.
  buider_spec = parent_test_spec.get('parent_test_spec', {})
  swarm_hashes = api.v8_tests._make_dummy_swarm_hashes(
      test[0] for test in buider_spec.get('tests', []))

  # Dummy GN args from compilation.
  gn_args = ['use_foo = true', 'also_interesting = "absolutely"']

  # Set up compilator build output.
  output_properties = {
    "compilator_properties": {
        "swarm_hashes": swarm_hashes,
        "gn_args": gn_args,
        **parent_test_spec,
    },
  }

  def test(name, *args):
    return api.test(
        name, api.buildbucket.try_build(builder='v8_foobar_rel'),
        api.properties(compilator_name='v8_foobar_compile_rel'), *args)

  yield test(
      'basic',
      subbuild_data(output_properties),
      api.step_data('Check', api.v8_tests.one_failure()),
      api.post_process(MustRun, 'Check'),
      api.post_process(MustRun, 'Test262'),
      api.expect_status('FAILURE'),
  )

  yield test(
      'missing_properties',
      subbuild_data({}, 'Compile failed', common_pb.FAILURE),
      api.post_process(DoesNotRun, 'Check'),
      api.post_process(DoesNotRun, 'Test262'),
      api.post_process(ResultReason, 'Compile failed'),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  yield test(
      'infra_failure',
      subbuild_data({}, 'Timeout', common_pb.INFRA_FAILURE),
      api.post_process(DoesNotRun, 'Check'),
      api.post_process(DoesNotRun, 'Test262'),
      api.post_process(ResultReason, 'Timeout'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(DropExpectation),
  )

  yield test(
      'no_subbuild',
      api.post_process(ResultReason, 'sub_build missing from step'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(DropExpectation),
  )

  yield test(
      'testing_canceled',
      subbuild_data(output_properties),
      api.runtime.global_shutdown_on_step('Check'),
      api.post_process(ResultReason, BUILD_CANCELED_SUMMARY),
      api.expect_status('CANCELED'),
      api.post_process(DropExpectation),
  )

  yield test(
      'subbuild_canceled',
      api.runtime.global_shutdown_on_step('compilator steps'),
      subbuild_data({}, '', common_pb.CANCELED),
      api.post_process(ResultReason, BUILD_CANCELED_SUMMARY),
      api.expect_status('CANCELED'),
      api.post_process(DropExpectation),
  )

  yield test(
      'subbuild_canceled_before_parent',
      subbuild_data({}, '', common_pb.CANCELED),
      api.post_process(ResultReason, BUILD_WRONGLY_CANCELED_SUMMARY),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(DropExpectation),
  )

  yield test(
      'no_tests',
      subbuild_data({'compilator_properties': {
          'parent_test_spec': {}
      }}),
      api.post_process(DoesNotRun, 'Check'),
      api.post_process(DoesNotRun, 'Test262'),
      api.post_process(ResultReason, 'No tests specified'),
      api.expect_status('FAILURE'),
      api.post_process(DropExpectation),
  )

  led_properties = {
      '$recipe_engine/led':
          led_properties_pb.InputProperties(
              led_run_id='fake-run-id',
          ),
  }

  build_proto_json = {
    'status': common_pb.SUCCESS,
    'summary': '',
    'output': {
      'properties': output_properties,
    },
  }

  yield test(
      'run_with_led',
      api.properties(**led_properties),
      api.step_data(
          'read build.proto.json',
          api.file.read_json(json_content=build_proto_json)),
      api.post_process(MustRun, 'initialization.trigger compilator.led get-builder'),
      api.post_process(MustRun, 'initialization.trigger compilator.led edit-cr-cl'),
      api.post_process(MustRun, 'initialization.trigger compilator.led launch'),
      api.post_process(MustRun, 'collect led compilator build'),
      api.post_process(MustRun, 'read build.proto.json'),
      api.post_process(MustRun, 'Check'),
      api.post_process(MustRun, 'Test262'),
      api.post_process(DropExpectation),
  )
