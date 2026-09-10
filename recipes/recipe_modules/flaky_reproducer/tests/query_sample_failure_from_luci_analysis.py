# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.recipe_api import Property

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import flaky_reproducer
from RECIPE_MODULES.recipe_engine import luci_analysis, properties, step


@dataclass
class DEPS(RecipeScriptApi):
  flaky_reproducer: flaky_reproducer.API
  luci_analysis: luci_analysis.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  luci_analysis: luci_analysis.TEST_API
  properties: properties.TEST_API


PROPERTIES = {
  'monorail_issue': Property(default=None, kind=str),
  'test_id': Property(default=None, kind=str),
}


def RunSteps(api: DEPS, monorail_issue, test_id):
  with api.step.nest('result') as presentation:
    build_id, test_id = (
      api.flaky_reproducer.query_sample_failure_from_luci_analysis(
        monorail_issue, test_id
      )
    )
    presentation.logs['build_id'] = build_id
    presentation.logs['test_id'] = test_id


from recipe_engine import post_process


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.properties(monorail_issue='123', test_id='test/1'),
    api.luci_analysis.lookup_bug(
      [
        'projects/chromium/rules/456',
      ],
      'chromium/123',
      parent_step_name='result.query_sample_failure_from_luci_analysis',
    ),
    api.luci_analysis.query_cluster_failures(
      [
        {
          # not picked, because it's a reviver build
          'test_id': 'test/1',
          'ingested_invocation_id': 'build-6',
          'count': 5,
          'variant': {
            'def': {'builder': 'runner', 'reviver_builder': 'Mac10.14 Tests'}
          },
        },
        {
          # not picked, because it's a presubmit build
          'test_id': 'test/1',
          'ingested_invocation_id': 'build-1',
          'presubmit_run': {
            'mode': 1,
          },
          'count': 100,
          'variant': {'def': {'builder': 'linux-rel'}},
        },
        {
          # not picked, because the failing count less than 50% (5).
          'test_id': 'test/1',
          'ingested_invocation_id': 'build-2',
          'count': 1,
          'variant': {'def': {'builder': 'Linux Tests'}},
        },
        {
          # not picked, because it's a suppressed builder.
          'test_id': 'test/1',
          'ingested_invocation_id': 'build-3',
          'count': 10,
          'variant': {'def': {'builder': 'android-pie-x86-rel'}},
        },
        {
          # not picked
          'test_id': 'test/1',
          'ingested_invocation_id': 'build-4',
          'count': 5,
        },
        {
          # not picked
          'test_id': 'test/1',
          'ingested_invocation_id': 'build-5',
          'count': 5,
        },
        {
          # picked, it's a preferred builder
          'test_id': 'test/1',
          'ingested_invocation_id': 'build-6',
          'count': 5,
          'variant': {'def': {'builder': 'Mac10.14 Tests'}},
        },
        {
          # not picked, because it doesn't match the test_id
          'test_id': 'test/2',
          'ingested_invocation_id': 'build-7',
          'count': 100,
          'variant': {'def': {'builder': 'Linux Tests'}},
        },
      ],
      'projects/chromium/clusters/rules/456',
      parent_step_name='result.query_sample_failure_from_luci_analysis',
    ),
    api.post_check(
      lambda check, steps: check(
        steps['result'].logs['build_id'] == '6',
        steps['result'].logs['build_id'],
      )
    ),
    api.post_check(
      lambda check, steps: check(
        steps['result'].logs['test_id'] == 'test/1',
        steps['result'].logs['test_id'],
      )
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'not rules',
    api.properties(
      monorail_issue='123',
    ),
    api.luci_analysis.lookup_bug(
      [],
      'chromium/123',
      parent_step_name='result.query_sample_failure_from_luci_analysis',
    ),
    api.post_check(
      post_process.SummaryMarkdown, 'No cluster associated with bug.'
    ),
    api.post_process(post_process.DropExpectation),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'not failures',
    api.properties(
      monorail_issue='123',
    ),
    api.luci_analysis.lookup_bug(
      [
        'projects/chromium/rules/456',
      ],
      'chromium/123',
      parent_step_name='result.query_sample_failure_from_luci_analysis',
    ),
    api.luci_analysis.query_cluster_failures(
      [],
      'projects/chromium/clusters/rules/456',
      parent_step_name='result.query_sample_failure_from_luci_analysis',
    ),
    api.post_check(
      post_process.SummaryMarkdown,
      'No failure found in the LUCI Analysis cluster.',
    ),
    api.post_process(post_process.DropExpectation),
    api.expect_status('FAILURE'),
  )
