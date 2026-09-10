# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_tests, test_utils
from RECIPE_MODULES.recipe_engine import (
  assertions,
  properties,
  resultdb,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_tests: chromium_tests.API
  properties: properties.API
  resultdb: resultdb.API
  step: step.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API


from recipe_engine.recipe_api import Property
from recipe_engine import post_process

from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  test_result as test_result_pb2,
)

from RECIPE_MODULES.build.test_utils import util
from RECIPE_MODULES.build.chromium_tests import steps

PROPERTIES = {
  'per_suffix_valid': Property(default={}),
  'per_suffix_failures': Property(default={}),
  'per_suffix_complete': Property(default={}),
  'expected_status': Property(default=''),
  'suffix': Property(default=''),
}


def RunSteps(
  api: DEPS,
  per_suffix_valid,
  per_suffix_failures,
  per_suffix_complete,
  expected_status,
  suffix,
):
  test_spec = steps.MockTestSpec.create(
    name='test_name',
    per_suffix_failures=per_suffix_failures,
    per_suffix_valid=per_suffix_valid,
    per_suffix_complete=per_suffix_complete,
  )
  test = test_spec.get_test(api.chromium_tests)
  # without patch looks at the rdb_results to figure out if all the failures are
  # still failing
  for failed_suffix, failed_tests in per_suffix_failures.items():
    test_invocations = {}
    for t in failed_tests:
      test_invocations['invocation/1234'] = api.resultdb.Invocation(
        test_results=[
          test_result_pb2.TestResult(test_id=t, expected=False),
        ]
      )
    results = util.RDBPerSuiteResults.create(
      test_invocations, 'test_name', 'prefix', 1
    )
    test.update_rdb_results(failed_suffix, results)
  api.assertions.assertEqual(test.get_status(suffix), expected_status)


def GenTests(api: TEST_DEPS):
  yield api.test(
    'all_invalid',
    api.properties(
      suffix='with patch',
      per_suffix_valid={
        '': False,
        'with patch': False,
        'retry shards with patch': False,
      },
      expected_status='Invalid',
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'all_incomplete',
    api.properties(
      suffix='with patch',
      per_suffix_complete={
        '': False,
        'with patch': False,
        'retry shards with patch': False,
      },
      expected_status='Incomplete',
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'ci_failed',
    api.properties(
      per_suffix_valid={
        '': True,
        'with patch': False,
        'retry shards with patch': False,
      },
      per_suffix_failures={
        '': ['testA'],
        'retry shards': ['testA'],
      },
      expected_status='Failure',
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'ci_success',
    api.properties(
      per_suffix_valid={
        '': True,
        'with patch': False,
        'retry shards with patch': False,
      },
      expected_status='Success',
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'try_success',
    api.properties(
      suffix='with patch',
      per_suffix_valid={
        'with patch': True,
      },
      expected_status='Success',
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'try_failure_with_patch_no_without_patch',
    api.properties(
      suffix='with patch',
      per_suffix_valid={
        '': False,
        'with patch': True,
        'retry shards with patch': False,
        'without patch': False,
      },
      per_suffix_failures={
        'with patch': ['testA'],
      },
      expected_status='Failure',
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'try_failure_with_patch_without_patch_exonerates',
    api.properties(
      suffix='with patch',
      per_suffix_valid={
        '': False,
        'with patch': True,
        'retry shards with patch': False,
        'without patch': True,
      },
      per_suffix_failures={
        'with patch': ['testA'],
        'without patch': ['testA'],
      },
      expected_status='Success',
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'try_failure_with_patch_without_patch_does_not_exonerate',
    api.properties(
      suffix='with patch',
      per_suffix_valid={
        '': False,
        'with patch': True,
        'retry shards with patch': False,
        'without patch': True,
      },
      per_suffix_failures={
        'with patch': ['testA'],
      },
      expected_status='Failure',
    ),
    api.post_process(post_process.DropExpectation),
  )
