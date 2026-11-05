# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from dataclasses import dataclass
import json

from recipe_engine import post_process
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_modules.build.chromium_orchestrator.properties import (
  InputProperties,
)
from RECIPE_MODULES.build import chromium_orchestrator
from RECIPE_MODULES.recipe_engine import buildbucket, properties, step


@dataclass
class DEPS(RecipeScriptApi):
  chromium_orchestrator: chromium_orchestrator.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  build_id = api.chromium_orchestrator.find_compilator_build_id(
    build_pb2.Build(
      id=1000,
      builder=dict(project='chromium', bucket='try', builder='win-rel'),
    )
  )
  api.step.empty('compilator build', step_text=str(build_id))


def GenTests(api: TEST_DEPS):
  compilator = api.properties(
    **{
      '$build/chromium_orchestrator': InputProperties(
        compilator='win-rel-compilator'
      )
    }
  )
  # SearchBuilds returns newest first: the without-patch compilator (created
  # later) comes before the with-patch one.
  without_patch = build_pb2.Build(
    id=1999, create_time=dict(seconds=200), status=common_pb.SUCCESS
  )

  def with_patch(status):
    return build_pb2.Build(
      id=2000, create_time=dict(seconds=100), status=status
    )

  def search_results(builds):
    return api.buildbucket.simulated_search_results(
      builds, step_name='find compilator build'
    )

  def check_predicate(check, steps):
    request = json.loads(steps['find compilator build'].logs['request'])
    predicate = request['requests'][0]['searchBuilds']['predicate']
    check(
      predicate
      == {
        'builder': {
          'project': 'chromium',
          'bucket': 'try',
          'builder': 'win-rel-compilator',
        },
        'childOf': '1000',
      }
    )

  yield api.test(
    'basic',
    compilator,
    search_results([without_patch, with_patch(common_pb.SUCCESS)]),
    api.post_check(check_predicate),
    api.post_check(post_process.StepTextEquals, 'compilator build', '2000'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'with_patch_failed',
    compilator,
    search_results([without_patch, with_patch(common_pb.FAILURE)]),
    api.post_check(
      post_process.SummaryMarkdownRE,
      'With-patch win-rel-compilator build 2000 for build 1000 did not succeed',
    ),
    api.post_check(post_process.DoesNotRun, 'compilator build'),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_compilator_build',
    compilator,
    search_results([]),
    api.post_check(
      post_process.SummaryMarkdownRE,
      'No win-rel-compilator build found for build 1000',
    ),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'missing_compilator_property',
    api.post_check(post_process.DoesNotRun, 'find compilator build'),
    api.post_check(post_process.SummaryMarkdownRE, 'Missing compilator input'),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
