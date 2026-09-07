# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from collections import defaultdict

from recipe_engine import post_process

from PB.go.chromium.org.luci.resultdb.proto.v1 import (test_result as
                                                       test_result_pb2)
from PB.go.chromium.org.luci.resultdb.proto.v1 import (common as common_pb2)

from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build.test_utils import util
from RECIPE_MODULES.depot_tools.tryserver import api as tryserver

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_tests, test_utils
from RECIPE_MODULES.depot_tools import gerrit
from RECIPE_MODULES.recipe_engine import (
    assertions,
    json,
    properties,
    resultdb,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  gerrit: gerrit.API
  json: json.API
  properties: properties.API
  resultdb: resultdb.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  json: json.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  test_specs = []
  for i in range(api.properties.get('num_failed_suites', 1)):
    test_specs.append(steps.MockTestSpec.create(name=f'fake_suite{i}'))
  test_suites = [s.get_test(api.chromium_tests) for s in test_specs]

  for suite in test_suites:
    var = common_pb2.Variant()
    var_def = getattr(var, 'def')
    var_def['test_suite'] = suite.name
    invocation_dict = {
        suite.name + '_inv_id':
            api.resultdb.Invocation(test_results=[
                test_result_pb2.TestResult(
                    test_id=suite.name + '_test_case',
                    status=test_result_pb2.FAIL,
                    expected=False,
                    variant=var)
            ])
    }
    per_suite_results = util.RDBPerSuiteResults.create(invocation_dict,
                                                       suite.name, suite.name,
                                                       1)
    suite.update_rdb_results('', per_suite_results)
  should_abort = api.test_utils._should_abort_retry(test_suites, '',
                                                    {'fake_suite0'})

  expected_should_abort = api.properties.get('expected_should_abort', False)
  api.assertions.assertEqual(should_abort, expected_should_abort)


def GenTests(api: TEST_DEPS):
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
