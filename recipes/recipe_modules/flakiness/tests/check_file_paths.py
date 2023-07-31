# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from RECIPE_MODULES.build.flakiness.utils import TestDefinition

DEPS = [
    'flakiness',
    'recipe_engine/assertions',
]


def RunSteps(api):
  affected_files = [
      'some/test/path/test.cc',
      'some/nontest/path/fake_base.cc',
  ]
  new_tests = [
      # file path matches. should be added in new tests to run.
      TestDefinition(
          test_id='ninja://some/test/run_test1',
          test_name='Test1',
          variant_hash='12345',
          duration_milliseconds=1,
          file_path='//some/test/path/test.cc',
      ),
      # no file_path defined. should be added in new tests to run.
      TestDefinition(
          test_id='ninja://some/test/run_test2',
          test_name='Test1',
          variant_hash='12345',
          duration_milliseconds=1,
      ),
      # file path does not match affected files
      TestDefinition(
          test_id='ninja://some/test/run_test3',
          test_name='Test3',
          variant_hash='12345',
          duration_milliseconds=1,
          file_path='//random_path/test.cc',
      ),
  ]
  final_result = api.flakiness.check_test_files(new_tests, affected_files)
  api.assertions.assertEqual(2, len(final_result))


def GenTests(api):
  yield api.test(
      'basic',
      api.flakiness(check_for_flakiness=True,),
      api.post_process(post_process.MustRun, 'Skipped tests'),
      api.post_process(post_process.DropExpectation),
  )
