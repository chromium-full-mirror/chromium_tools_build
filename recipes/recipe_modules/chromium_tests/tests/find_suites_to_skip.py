# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test to ensure the correctness of find_suites_to_skip"""

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.config import List
from recipe_engine.recipe_api import Property

from google.protobuf import timestamp_pb2

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_tests
from RECIPE_MODULES.recipe_engine import (
  assertions,
  buildbucket,
  cq,
  properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  buildbucket: buildbucket.API
  chromium_tests: chromium_tests.API
  cq: cq.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  cq: cq.TEST_API
  properties: properties.TEST_API


PROPERTIES = {
  'expected': Property(kind=set),
}


def RunSteps(api: DEPS, expected):
  actual = api.chromium_tests.find_suites_to_skip()
  api.assertions.assertSetEqual(actual, expected)


def GenTests(api: TEST_DEPS):

  def _create_previous_build(
    test_statuses=None, flake_endorser_flakes=None, flake_endorser_invalids=None
  ):
    reusable_build = build_pb2.Build(
      id=1234,
      status='SUCCESS',
      create_time=timestamp_pb2.Timestamp(seconds=1598338800),
      output=build_pb2.Build.Output(),
    )
    reusable_build.output.properties['test_status'] = (
      test_statuses if test_statuses else {}
    )
    if flake_endorser_flakes or flake_endorser_invalids:
      reusable_build.output.properties['flake_endorser_rejections'] = {}
      if flake_endorser_flakes:
        reusable_build.output.properties['flake_endorser_rejections'][
          'flaky_suites'
        ] = flake_endorser_flakes
      if flake_endorser_invalids:
        reusable_build.output.properties['flake_endorser_rejections'][
          'invalid_suites'
        ] = flake_endorser_invalids
    return reusable_build

  yield api.test(
    'basic',
    api.buildbucket.try_build(
      tags=[
        common_pb2.StringPair(key='cq_equivalent_cl_group_key', value='111')
      ]
    ),
    api.cq(run_mode='FULL_RUN'),
    api.properties(expected=set()),
    api.buildbucket.simulated_search_results(
      [_create_previous_build()],
      step_name='check previous builds for skippable test suites.find equivalent patchset builds',
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'shadow_bucket_searches_real_bucket',
    api.buildbucket.try_build(
      bucket='try.shadow',
      tags=[
        common_pb2.StringPair(key='cq_equivalent_cl_group_key', value='111')
      ],
    ),
    api.cq(run_mode='FULL_RUN'),
    api.properties(expected=set()),
    api.buildbucket.simulated_search_results(
      [],
      step_name='check previous builds for skippable test suites.find equivalent patchset builds',
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'no_previous_build',
    api.buildbucket.try_build(
      tags=[
        common_pb2.StringPair(key='cq_equivalent_cl_group_key', value='111')
      ]
    ),
    api.cq(run_mode='FULL_RUN'),
    api.properties(expected=set()),
    api.buildbucket.simulated_search_results(
      [],
      step_name='check previous builds for skippable test suites.find equivalent patchset builds',
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'ignores_not_cq',
    api.buildbucket.try_build(
      tags=[
        common_pb2.StringPair(key='cq_equivalent_cl_group_key', value='111')
      ]
    ),
    api.properties(expected=set()),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'skips_successfull_test',
    api.buildbucket.try_build(
      tags=[
        common_pb2.StringPair(key='cq_equivalent_cl_group_key', value='111')
      ]
    ),
    api.cq(run_mode='FULL_RUN'),
    api.properties(expected={'testA'}),
    api.buildbucket.simulated_search_results(
      [_create_previous_build({'testA': 'Success'})],
      step_name='check previous builds for skippable test suites.find equivalent patchset builds',
    ),
    api.post_process(
      post_process.StepTextContains,
      'check previous builds for skippable test suites',
      ['testA'],
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'does_not_skip_unsuccessfull_test',
    api.buildbucket.try_build(
      tags=[
        common_pb2.StringPair(key='cq_equivalent_cl_group_key', value='111')
      ]
    ),
    api.cq(run_mode='FULL_RUN'),
    api.properties(expected=set()),
    api.buildbucket.simulated_search_results(
      [_create_previous_build({'testA': 'Failure'})],
      step_name='check previous builds for skippable test suites.find equivalent patchset builds',
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'skips_single_successfull_test',
    api.buildbucket.try_build(
      tags=[
        common_pb2.StringPair(key='cq_equivalent_cl_group_key', value='111')
      ]
    ),
    api.cq(run_mode='FULL_RUN'),
    api.properties(expected={'testA'}),
    api.buildbucket.simulated_search_results(
      [
        _create_previous_build({'testA': 'Success'}),
        _create_previous_build({'testA': 'Failure'}),
      ],
      step_name='check previous builds for skippable test suites.find equivalent patchset builds',
    ),
    api.post_process(
      post_process.StepTextContains,
      'check previous builds for skippable test suites',
      ['testA'],
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'runs_flake_endorser_flakes',
    api.buildbucket.try_build(
      tags=[
        common_pb2.StringPair(key='cq_equivalent_cl_group_key', value='111')
      ]
    ),
    api.cq(run_mode='FULL_RUN'),
    api.properties(expected=set()),
    api.buildbucket.simulated_search_results(
      [
        _create_previous_build(
          {'testA': 'Success'}, flake_endorser_flakes=['testA']
        ),
      ],
      step_name='check previous builds for skippable test suites.find equivalent patchset builds',
    ),
    # Ensure no "skippable test" text is printed
    api.post_process(
      post_process.StepTextEquals,
      'check previous builds for skippable test suites',
      '',
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'runs_flake_endorser_invalids',
    api.buildbucket.try_build(
      tags=[
        common_pb2.StringPair(key='cq_equivalent_cl_group_key', value='111')
      ]
    ),
    api.cq(run_mode='FULL_RUN'),
    api.properties(expected=set()),
    api.buildbucket.simulated_search_results(
      [
        _create_previous_build(
          {'testA': 'Success'}, flake_endorser_invalids=['testA']
        ),
      ],
      step_name='check previous builds for skippable test suites.find equivalent patchset builds',
    ),
    api.post_process(
      post_process.StepTextEquals,
      'check previous builds for skippable test suites',
      '',
    ),
    api.post_process(post_process.DropExpectation),
  )
