# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_tests, chromium_tests_builder_config
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import json


@dataclass
class DEPS(RecipeScriptApi):
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  json: json.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  json: json.TEST_API


def RunSteps(api: DEPS):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  if api.tryserver.is_tryserver:
    return api.chromium_tests.trybot_steps(builder_id, builder_config)
  build_result, _ = api.chromium_tests.main_waterfall_steps(
      builder_id, builder_config)
  return build_result


def GenTests(api: TEST_DEPS):
  builder_db = ctbc.BuilderDatabase.create({
      'test-group': {
          'test-builder':
              ctbc.BuilderSpec.create(
                  chromium_config='chromium',
                  gclient_config='chromium',
              ),
      }
  })
  try_db = ctbc.TryDatabase.create({
      'test-try-group': {
          'test-try-builder':
              ctbc.TrySpec.create_for_single_mirror(
                  builder_group='test-group',
                  buildername='test-builder',
              ),
      }
  })

  def common_test_data(test_spec):
    return api.chromium_tests.read_targets_spec('test-group', {
        'test-builder': {
            'scripts': [test_spec],
        },
    })

  def ci_build(test_spec, **kwargs):
    t = api.chromium_tests_builder_config.ci_build(
        builder_group='test-group',
        builder='test-builder',
        builder_db=builder_db,
        **kwargs)
    t += common_test_data(test_spec)
    return t

  def try_build(test_spec, **kwargs):
    t = api.chromium_tests_builder_config.try_build(
        builder_group='test-group',
        builder='test-builder',
        builder_db=builder_db,
        try_db=try_db,
        **kwargs)
    t += common_test_data(test_spec)
    return t

  yield api.test(
      'basic',
      ci_build(test_spec={
          'name': 'base_unittests',
          'script': 'gtest_test.py',
      }),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          'vpython3',
          '[CACHE]/builder/src/testing/scripts/gtest_test.py',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci-with-args',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'script': 'gtest_test.py',
              'args': ['common-arg'],
              'precommit_args': ['try-arg'],
              'non_precommit_args': ['ci-arg'],
          }),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          '--args',
          api.json.dumps(['common-arg', 'ci-arg']),
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'try-with-args',
      try_build(
          test_spec={
              'name': 'base_unittests',
              'script': 'gtest_test.py',
              'args': ['common-arg'],
              'precommit_args': ['try-arg'],
              'non_precommit_args': ['ci-arg'],
          }),
      api.post_process(
          post_process.StepCommandContains,
          'base_unittests (with patch)',
          [
              '--args',
              api.json.dumps(['common-arg', 'try-arg']),
          ],
      ),
      api.post_process(post_process.DropExpectation),
  )
