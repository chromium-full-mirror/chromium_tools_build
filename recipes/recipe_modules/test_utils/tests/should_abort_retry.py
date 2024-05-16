# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from collections import defaultdict

from recipe_engine import post_process

from PB.go.chromium.org.luci.resultdb.proto.v1 import (test_result as
                                                       test_result_pb2)
from PB.go.chromium.org.luci.resultdb.proto.v1 import (common as common_pb2)

from RECIPE_MODULES.build.test_utils import util
from RECIPE_MODULES.depot_tools.tryserver import api as tryserver


DEPS = [
    'chromium',
    'test_utils',
    'depot_tools/gerrit',
    'recipe_engine/assertions',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/resultdb',
]


def RunSteps(api):
  num_failed_suites = api.properties.get('num_failed_suites', 1)
  failed_suites = ['fake_suite' + str(i) for i in range(num_failed_suites)]

  rdb_suite_results = []
  for suite in failed_suites:
    var = common_pb2.Variant()
    var_def = getattr(var, 'def')
    var_def['test_suite'] = suite
    invocation_dict = {
        suite + '_inv_id':
            api.resultdb.Invocation(test_results=[
                test_result_pb2.TestResult(
                    test_id=suite + '_test_case',
                    status=test_result_pb2.FAIL,
                    expected=False,
                    variant=var)
            ])
    }
    rdb_suite_results.append(
        util.RDBPerSuiteResults.create(invocation_dict, suite, suite, 1))
  rdb_results = util.RDBResults.create(rdb_suite_results)
  should_abort = api.test_utils._should_abort_retry(rdb_results,
                                                    {'fake_suite0'})

  expected_should_abort = api.properties.get('expected_should_abort', False)
  api.assertions.assertEqual(should_abort, expected_should_abort)


def GenTests(api):
  yield api.test(
      'disable-retries-footer', api.chromium.try_build(),
      api.properties(expected_should_abort=True),
      api.step_data(
          'parse description',
          api.json.output({tryserver.constants.SKIP_RETRY_FOOTER: 'true'})),
      api.post_check(post_process.MustRun, 'retries disabled'),
      api.post_process(post_process.DropExpectation))

  yield api.test(
      'disable-retries-footer-failure',
      api.chromium.try_build(),
      api.step_data('gerrit changes', retcode=1),
      api.post_check(post_process.DoesNotRun, 'retries disabled'),
      api.post_check(post_process.MustRun, 'failure getting footers'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'resultdb-retry-abort',
      api.chromium.try_build(),
      api.properties(
          num_failed_suites=11,
          expected_should_abort=True,
          **{
              '$build/test_utils': {
                  'min_failed_suites_to_skip_retry': 10,
              },
          }),
      api.post_check(post_process.MustRun, 'abort retry'),
      # fake_suite0 should not be here as it is allowed.
      api.post_check(
          post_process.StepTextEquals, 'abort retry',
          ('<br/>skip retrying because there are >= 10 test suites '
           'with test failures and it most likely indicates a '
           'problem with the CL. These suites being:<br/>'
           f'{"<br/>".join("fake_suite"+str(i) for i in range(1, 11))}')),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'resultdb-retry-continue',
      api.chromium.try_build(),
      api.properties(
          num_failed_suites=10,
          expected_should_abort=False,
          **{
              '$build/test_utils': {
                  'min_failed_suites_to_skip_retry': 11,
              },
          }),
      api.post_check(post_process.MustRun, 'proceed with retry'),
      api.post_process(post_process.DropExpectation),
  )
