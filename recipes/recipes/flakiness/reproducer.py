# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from PB.recipes.build.flakiness.reproducer import InputProperties
from PB.go.chromium.org.luci.resultdb.proto.v1 import resultdb as resultdb_pb2

PROPERTIES = InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import flaky_reproducer
from RECIPE_MODULES.recipe_engine import (
  json,
  properties,
  raw_io,
  resultdb,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  flaky_reproducer: flaky_reproducer.API
  json: json.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API
  resultdb: resultdb.TEST_API


def RunSteps(api: DEPS, properties):
  api.flaky_reproducer.set_config(properties.config or 'auto')
  return api.flaky_reproducer.run(
    task_id=properties.task_id,
    build_id=properties.build_id,
    test_name=properties.test_name,
    test_id=properties.test_id,
    verify_on_builders=properties.verify_on_builders,
    monorail_issue=properties.monorail_issue,
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'cannot_retrieve_invocation',
    api.properties(
      build_id=2**63 - 1,
      test_name="MockUnitTests.FailTest",
      config="manual",
    ),
    api.resultdb.query_test_results(resultdb_pb2.QueryTestResultsResponse()),
    api.post_check(post_process.SummaryMarkdown, 'Cannot find TestResult.'),
    api.post_process(post_process.DropExpectation),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'must_have_task_id_or_build_id',
    api.properties(
      task_id=None,
      build_id=None,
      test_id="not-exists",
    ),
    api.post_check(
      post_process.SummaryMarkdown, 'Must specify task_id or build_id.'
    ),
    api.post_process(post_process.DropExpectation),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'must_have_test_id_or_test_name',
    api.properties(
      task_id='some-task',
      test_id=None,
      test_name=None,
    ),
    api.post_check(
      post_process.SummaryMarkdown, 'Must specify test_name or test_id.'
    ),
    api.post_process(post_process.DropExpectation),
    api.expect_status('FAILURE'),
  )
