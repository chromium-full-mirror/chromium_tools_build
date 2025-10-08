# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2

from recipe_engine.post_process import (DropExpectation, MustRun,
                                        SummaryMarkdown)

from RECIPE_MODULES.build.v8_orchestrator.api import (
    BUILD_CANCELED_SUMMARY, BUILD_WRONGLY_CANCELED_SUMMARY)

from google.protobuf import json_format
from google.protobuf import struct_pb2

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'v8_orchestrator',
]


def RunSteps(api):

  def fake_test_initialization():
    api.step.empty('testing initialized')

  comp_props, maybe_result = api.v8_orchestrator.orchestrated_compilation(
      'fake_compilator', fake_test_initialization)
  if maybe_result:
    return maybe_result
  api.step.empty('fake test run')
  return result_pb2.RawResult(
      status=common_pb.SUCCESS, summary_markdown=comp_props['summary'])


def GenTests(api):

  def subbuild_data(output_properties=None,
                    summary='pass',
                    status=common_pb.SUCCESS):
    output_properties = output_properties or {}
    sub_build = build_pb2.Build(
        id=54321,
        status=status,
        summary_markdown=summary,
        output=dict(
            properties=json_format.Parse(
                api.json.dumps(output_properties), struct_pb2.Struct())))
    return api.step_data('compilator steps', api.step.sub_build(sub_build))

  yield api.test(
      'basic',
      api.buildbucket.try_build(builder='v8_foobar'),
      subbuild_data({'compilator_properties': {
          'summary': 'All good!'
      }}),
      api.post_process(MustRun, 'initialization.trigger compilator'),
      api.post_process(MustRun, 'initialization.testing initialized'),
      api.post_process(MustRun, 'compilator steps'),
      api.post_process(MustRun, 'fake test run'),
      api.post_process(SummaryMarkdown, 'All good!'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'missing properties',
      api.buildbucket.try_build(builder='v8_foobar'),
      subbuild_data(
          summary='compilation failed', status=common_pb.INFRA_FAILURE),
      api.post_process(MustRun, 'initialization.trigger compilator'),
      api.post_process(MustRun, 'initialization.testing initialized'),
      api.post_process(MustRun, 'compilator steps'),
      api.post_process(SummaryMarkdown, 'compilation failed'),
      api.post_process(DropExpectation),
      status='INFRA_FAILURE',
  )

  yield api.test(
      'subbuild_canceled',
      api.runtime.global_shutdown_on_step('compilator steps'),
      subbuild_data({}, 'canceled', common_pb.CANCELED),
      api.post_process(SummaryMarkdown, BUILD_CANCELED_SUMMARY),
      api.post_process(DropExpectation),
      status='CANCELED',
  )

  yield api.test(
      'subbuild_canceled_before_parent',
      subbuild_data({}, 'canceled', common_pb.CANCELED),
      api.post_process(SummaryMarkdown, BUILD_WRONGLY_CANCELED_SUMMARY),
      api.post_process(DropExpectation),
      status='INFRA_FAILURE',
  )
