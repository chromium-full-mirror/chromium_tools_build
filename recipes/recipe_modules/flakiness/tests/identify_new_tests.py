# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr

from recipe_engine import post_process
from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import (
  builder_common as builder_common_pb2,
)
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  test_result as test_result_pb2,
)
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  test_metadata as test_metadata_pb2,
)
from PB.go.chromium.org.luci.analysis.proto.v1 import test_history
from PB.go.chromium.org.luci.analysis.proto.v1 import test_verdict

from recipe_engine import recipe_api
from recipe_engine import recipe_test_api

from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build.test_utils import util

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_tests, flakiness, test_utils
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  assertions,
  buildbucket,
  file,
  json,
  luci_analysis,
  properties,
  resultdb,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  buildbucket: buildbucket.API
  chromium_tests: chromium_tests.API
  file: file.API
  flakiness: flakiness.API
  json: json.API
  luci_analysis: luci_analysis.API
  properties: properties.API
  resultdb: resultdb.API
  step: step.API
  test_utils: test_utils.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  file: file.TEST_API
  flakiness: flakiness.TEST_API
  luci_analysis: luci_analysis.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  test_objects = []
  for i in range(6):
    inv_bundle = {}
    suite_name = 'some test %s' % str(i)
    inv = "invocations/{}".format(i + 1)
    test_id = 'ninja://sample/test:some_test/TestSuite.Test' + str(i)
    test_id_prefix = 'ninja://sample/test:some_test/'
    # To test the case when test id is not complete.
    if i == 4:
      test_id = 'TestSuite.Test' + str(i)
      test_id_prefix = None
    # To test the case when test id complete but test_id_prefix is absent.
    if i == 2:
      test_id_prefix = None
    test_results = [
      test_result_pb2.TestResult(
        test_id=test_id,
        variant_hash='{}hash'.format(i),
        expected=False,
        status=test_result_pb2.PASS,
        test_metadata=test_metadata_pb2.TestMetadata(
          location=test_metadata_pb2.TestLocation(
            repo='https://chromium.googlesource.com/chromium/src',
            file_name='//sample/test/some_test_%s.cc' % str(i),
          )
        ),
      ),
    ]
    inv_bundle[inv] = api.resultdb.Invocation(test_results=test_results)
    rdb_suite_results = util.RDBPerSuiteResults.create(
      inv_bundle, suite_name, test_id_prefix, 1
    )
    test_spec = steps.SwarmingGTestTestSpec.create(
      suite_name,
      test_id_prefix=test_id_prefix,
      isolate_profile_data=api.properties.get('isolate_profile_data', False),
    )
    # For coverage that the suite opts out Flake Endorser.
    if i == 5:
      test_spec = attr.evolve(test_spec, check_flakiness_for_new_tests=False)
    test_object = test_spec.get_test(api.chromium_tests)
    test_object.update_rdb_results('with patch', rdb_suite_results)
    test_objects.append(test_object)

  new_tests = {
    'ninja://sample/test:some_test/TestSuite.Test0_0hash',
    'ninja://sample/test:some_test/TestSuite.Test2_2hash',
    'ninja://sample/test:some_test/TestSuite.Test3_3hash',
    'TestSuite.Test4_4hash',
  }

  found_tests = api.flakiness.identify_new_tests(test_objects)
  if found_tests:
    found_tests = {
      str('_'.join([t.test_id, t.variant_hash])) for t in found_tests
    }
    api.assertions.assertEqual(new_tests, found_tests)


def GenTests(api: TEST_DEPS):
  basic_build = build_pb2.Build(
    builder=builder_common_pb2.BuilderID(
      builder='Builder', project='chromium', bucket='try'
    ),
    infra=build_pb2.BuildInfra(
      resultdb=build_pb2.BuildInfra.ResultDB(invocation='invocations/100')
    ),
    input=build_pb2.Build.Input(
      gerrit_changes=[
        common_pb2.GerritChange(
          host='chromium-review.googlesource.com',
          project='chromium/src',
          change=10,
          patchset=3,
        )
      ],
    ),
  )

  test1_history_res = test_history.QueryTestHistoryResponse(
    verdicts=[
      test_verdict.TestVerdict(
        test_id='ninja://sample/test:some_test/TestSuite.Test1',
        variant_hash='1hash',
        invocation_id='some_other_invocations',
        status=test_verdict.PASS,
      ),
    ],
    next_page_token='dummy_token',
  )
  empty_history_res = test_history.QueryTestHistoryResponse(
    verdicts=[], next_page_token='dummy_token'
  )

  yield api.test(
    'basic',
    api.buildbucket.build(basic_build),
    api.flakiness(
      check_for_flakiness=True,
    ),
    api.luci_analysis.query_test_history(
      test1_history_res,
      'ninja://sample/test:some_test/TestSuite.Test1',
      parent_step_name='searching_for_new_tests',
    ),
    api.luci_analysis.query_test_history(
      empty_history_res,
      'ninja://sample/test:some_test/TestSuite.Test0',
      parent_step_name='searching_for_new_tests',
    ),
    api.luci_analysis.query_test_history(
      empty_history_res,
      'ninja://sample/test:some_test/TestSuite.Test2',
      parent_step_name='searching_for_new_tests',
    ),
    api.luci_analysis.query_test_history(
      empty_history_res,
      'ninja://sample/test:some_test/TestSuite.Test3',
      parent_step_name='searching_for_new_tests',
    ),
    api.luci_analysis.query_test_history(
      empty_history_res,
      'TestSuite.Test4',
      parent_step_name='searching_for_new_tests',
    ),
    api.post_process(
      post_process.MustRun,
      (
        'searching_for_new_tests.Test history query rpc call for '
        'TestSuite.Test4'
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'cross reference infra failure',
    api.buildbucket.build(basic_build),
    api.flakiness(
      check_for_flakiness=True,
    ),
    api.step_data(
      'searching_for_new_tests.process precomputed test history',
      api.file.read_json(
        [
          {
            'test_id': 'ninja://sample/test:some_test/TestSuite.Test2',
            'variant_hash': '2hash',
            'invocation': ['invocation/3'],
          },
          {
            'test_id': 'ninja://sample/test:some_test/TestSuite.Test0',
            'variant_hash': '0hash',
            'invocation': ['invocation/1'],
          },
          {
            'test_id': 'ninja://sample/test:some_test/TestSuite.Test1',
            'variant_hash': '1hash',
            'invocation': ['invocation/3'],
          },
          {
            'test_id': 'ninja://sample/test:some_test/TestSuite.Test3',
            'variant_hash': '3hash',
            'invocation': ['invocation/1'],
          },
        ]
      ),
    ),
    api.override_step_data(
      'searching_for_new_tests.Test history query rpc call for TestSuite.Test4',
      retcode=1,
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'non-existent-try-builder',
    api.buildbucket.try_build(
      'chromium',
      'mac',
      git_repo='https://chromium.googlesource.com/chromium/src',
      change_number=91827,
      patch_set=1,
    ),
    api.flakiness(
      check_for_flakiness=True,
    ),
    api.override_step_data(
      'searching_for_new_tests.gsutil download', retcode=1
    ),
    api.post_process(
      post_process.StepTextEquals,
      'searching_for_new_tests',
      'The current try builder may not have test data precomputed.',
    ),
    api.post_process(
      post_process.DoesNotRunRE,
      '.*unpack.*',
      '.*process precomputed test history.*',
    ),
    # overall build status should remain unaffected by this.
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'infra_failure_json_parsing',
    api.buildbucket.try_build(
      'chromium',
      'mac',
      git_repo='https://chromium.googlesource.com/chromium/src',
      change_number=91827,
      patch_set=1,
    ),
    api.flakiness(
      check_for_flakiness=True,
    ),
    api.override_step_data(
      'searching_for_new_tests.process precomputed test history', retcode=1
    ),
    api.post_process(
      post_process.StepTextEquals,
      'searching_for_new_tests',
      (
        'Failed to parse the precomputed test history. '
        'Aborting the flakiness check.'
      ),
    ),
    api.post_process(post_process.StepException, 'searching_for_new_tests'),
    # overall build status should remain unaffected by this.
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no preliminary new tests',
    api.buildbucket.build(basic_build),
    api.flakiness(
      check_for_flakiness=True,
    ),
    api.step_data(
      # All build tests are returned from history.
      'searching_for_new_tests.process precomputed test history',
      api.file.read_json(
        [
          {
            'test_id': 'ninja://sample/test:some_test/TestSuite.Test0',
            'variant_hash': '0hash',
            'invocation': ['invocation/3'],
          },
          {
            'test_id': 'ninja://sample/test:some_test/TestSuite.Test1',
            'variant_hash': '1hash',
            'invocation': ['invocation/1'],
          },
          {
            'test_id': 'ninja://sample/test:some_test/TestSuite.Test2',
            'variant_hash': '2hash',
            'invocation': ['invocation/1'],
          },
          {
            'test_id': 'ninja://sample/test:some_test/TestSuite.Test3',
            'variant_hash': '3hash',
            'invocation': ['invocation/1'],
          },
          {
            'test_id': 'TestSuite.Test4',
            'variant_hash': '4hash',
            'invocation': ['invocation/1'],
          },
        ]
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'led',
    api.buildbucket.build(basic_build),
    api.flakiness(
      check_for_flakiness=True,
    ),
    api.properties(
      **{
        '$recipe_engine/led': {
          'shadowed_bucket': 'try-real',
        }
      }
    ),
    api.step_data(
      # All build tests are returned from history.
      'searching_for_new_tests.process precomputed test history',
      api.file.read_json(
        [
          {
            'test_id': 'ninja://sample/test:some_test/TestSuite.Test0',
            'variant_hash': '0hash',
            'invocation': ['invocation/3'],
          },
          {
            'test_id': 'ninja://sample/test:some_test/TestSuite.Test1',
            'variant_hash': '1hash',
            'invocation': ['invocation/1'],
          },
          {
            'test_id': 'ninja://sample/test:some_test/TestSuite.Test2',
            'variant_hash': '2hash',
            'invocation': ['invocation/1'],
          },
          {
            'test_id': 'ninja://sample/test:some_test/TestSuite.Test3',
            'variant_hash': '3hash',
            'invocation': ['invocation/1'],
          },
          {
            'test_id': 'TestSuite.Test4',
            'variant_hash': '4hash',
            'invocation': ['invocation/1'],
          },
        ]
      ),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'searching_for_new_tests.process precomputed test history',
      ['copy', '[CLEANUP]/flake_endorser/chromium/try-real/Builder.json'],
    ),
    api.post_process(post_process.DropExpectation),
  )
