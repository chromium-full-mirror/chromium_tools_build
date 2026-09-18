# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, SummaryMarkdownRE
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  test_result as test_result_pb2,
)
from PB.go.chromium.org.luci.resultdb.proto.v1 import common as resultdb_common
from RECIPE_MODULES.build.chromium_tests import steps
import copy

DEPS = [
  'flakiness',
  'recipe_engine/properties',
]


def RunSteps(api):
  class MockTest:
    def __init__(self, name, is_experimental=False):
      self.name = name
      self.is_experimental = is_experimental
      self._rdb_results = {}

    def get_rdb_results(self, suffix):
      return self._rdb_results.get(suffix)

  class MockRDBResults:
    def __init__(self, all_tests, variant_hash):
      self.all_tests = all_tests
      self.variant_hash = variant_hash

  class MockRDBTest:
    def __init__(self, test_id, test_name, unexpected_unpassed):
      self.test_id = test_id
      self.test_name = test_name
      self.unexpected_unpassed = unexpected_unpassed

    def unexpected_unpassed_count(self):
      return self.unexpected_unpassed

    def total_test_count(self):
      return 1

  test_case = api.properties.get('test_case')

  if test_case == 'basic':
    test_obj = MockTest('browser_tests')
    test_obj._rdb_results['check flakiness shard #0'] = MockRDBResults(
      all_tests=[
        MockRDBTest('ninja://browser_tests/Test:Test1', 'Test:Test1', 1)
      ],
      variant_hash='fake_hash',
    )
    test_obj_exp = MockTest('experimental_tests', is_experimental=True)
    test_obj_exp._rdb_results['check flakiness shard #0'] = MockRDBResults(
      all_tests=[
        MockRDBTest('ninja://browser_tests/Test:TestExp', 'Test:TestExp', 1)
      ],
      variant_hash='fake_hash',
    )
    suffix_suites = {'check flakiness shard #0': [test_obj, test_obj_exp]}
    return api.flakiness.check_run_results(suffix_suites)

  if test_case == 'empty_results':
    test_obj_empty = MockTest('empty_tests')
    test_obj_empty._rdb_results['check flakiness shard #0'] = MockRDBResults(
      all_tests=[], variant_hash='fake_hash'
    )
    suffix_suites_empty = {'check flakiness shard #0': [test_obj_empty]}
    return api.flakiness.check_run_results(suffix_suites_empty)

  if test_case == 'experimental_only':
    test_obj_exp_only = MockTest('experimental_only', is_experimental=True)
    test_obj_exp_only._rdb_results['check flakiness shard #0'] = MockRDBResults(
      all_tests=[
        MockRDBTest(
          'ninja://browser_tests/Test:TestExpOnly', 'Test:TestExpOnly', 1
        )
      ],
      variant_hash='fake_hash',
    )
    suffix_suites_exp = {'check flakiness shard #0': [test_obj_exp_only]}
    return api.flakiness.check_run_results(suffix_suites_exp)


def GenTests(api):
  yield api.test(
    'basic',
    api.properties(test_case='basic'),
    api.expect_status('FAILURE'),
    api.post_process(
      SummaryMarkdownRE,
      r'.*Some new test\(s\) added from your CL appear to be flaky.*',
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'empty_results',
    api.properties(test_case='empty_results'),
    api.expect_status('FAILURE'),
    api.post_process(SummaryMarkdownRE, r'.*didn\'t produce test results.*'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'experimental_only',
    api.properties(test_case='experimental_only'),
    api.expect_status('SUCCESS'),
    api.post_process(DropExpectation),
  )
