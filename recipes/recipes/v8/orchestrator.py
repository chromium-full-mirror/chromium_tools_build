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
                                        SummaryMarkdown)
from recipe_engine.recipe_api import Property

from google.protobuf import json_format
from google.protobuf import struct_pb2

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import isolate, v8_orchestrator, v8_tests
from RECIPE_MODULES.depot_tools import gitiles, tryserver
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cipd,
    file,
    json as json_module,
    led,
    path,
    properties,
    runtime,
    step,
    swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  cipd: cipd.API
  file: file.API
  gitiles: gitiles.API
  isolate: isolate.API
  json: json_module.API
  led: led.API
  path: path.API
  properties: properties.API
  runtime: runtime.API
  step: step.API
  swarming: swarming.API
  tryserver: tryserver.API
  v8_orchestrator: v8_orchestrator.API
  v8_tests: v8_tests.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  json: json_module.TEST_API
  properties: properties.TEST_API
  step: step.TEST_API
  swarming: swarming.TEST_API
  v8_tests: v8_tests.TEST_API

PROPERTIES = {
    # Name of the compilator trybot to use.
    'compilator_name': Property(kind=str),
}

BUILD_CANCELED_SUMMARY = 'Build was canceled.'
BUILD_WRONGLY_CANCELED_SUMMARY = (
    'Compilator was canceled before the parent orchestrator was canceled.')

EXONERATE_FLAKES_MAX = 3


def orchestrator_steps(api: DEPS, compilator_name):
  v8 = api.v8_tests

  def initialize_v8_testing():
    # Initialize V8 testing.
    v8.set_config('v8')
    v8.read_cl_footer_flags()
    v8.load_static_test_configs()

    api.swarming.ensure_client()
    v8.set_up_swarming()

  comp_props, maybe_result = api.v8_orchestrator.orchestrated_compilation(
      compilator_name, initialize_v8_testing)
  if maybe_result:
    return maybe_result

  # Initialize the test specs the compilator retrieved from the checkout.
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
  test_results = v8.runtests(None, tests)

  status = common_pb.SUCCESS
  summary_markdown = None
  if test_results.has_failures:
    status = common_pb.FAILURE
    summary_markdown = 'Failures in tryjob.'
  elif len(test_results.flakes) > EXONERATE_FLAKES_MAX:
    status = common_pb.FAILURE
    summary_markdown = 'Too many flakes in tryjob.'
  return result_pb2.RawResult(status=status, summary_markdown=summary_markdown)


def RunSteps(api: DEPS, compilator_name):
  try:
    return orchestrator_steps(api, compilator_name)
  finally:
    if api.runtime.in_global_shutdown:
      # pylint: disable=lost-exception
      # Cancellation can cause all sorts of spurious exceptions.
      return result_pb2.RawResult(
          status=common_pb.CANCELED,
          summary_markdown=BUILD_CANCELED_SUMMARY)


def GenTests(api: TEST_DEPS):
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

  def test(name, *args, **kwargs):
    return api.test(
        name, api.buildbucket.try_build(builder='v8_foobar_rel'),
        api.properties(compilator_name='v8_foobar_compile_rel'),
        *args, **kwargs)

  yield test(
      'basic',
      subbuild_data(output_properties),
      api.step_data('Check', api.v8_tests.one_failure()),
      api.post_process(MustRun, 'Check'),
      api.post_process(MustRun, 'Test262'),
      api.post_process(SummaryMarkdown, 'Failures in tryjob.'),
      status='FAILURE',
  )

  yield test(
      'too_many_flakes',
      subbuild_data(output_properties),
      api.step_data('Check', api.v8_tests.flakes(count=4)),
      api.post_process(MustRun, 'Check'),
      api.post_process(MustRun, 'Test262'),
      api.post_process(SummaryMarkdown, 'Too many flakes in tryjob.'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield test(
      'missing_properties',
      subbuild_data({}, 'Compile failed', common_pb.FAILURE),
      api.post_process(DoesNotRun, 'Check'),
      api.post_process(DoesNotRun, 'Test262'),
      api.post_process(SummaryMarkdown, 'Compile failed'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield test(
      'no_subbuild',
      api.post_process(SummaryMarkdown, 'sub_build missing from step'),
      api.post_process(DropExpectation),
      status='INFRA_FAILURE',
  )

  yield test(
      'no_tests',
      subbuild_data({'compilator_properties': {
          'parent_test_spec': {}
      }}),
      api.post_process(DoesNotRun, 'Check'),
      api.post_process(DoesNotRun, 'Test262'),
      api.post_process(SummaryMarkdown, 'No tests specified'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
