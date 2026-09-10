# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.recipe_engine import assertions


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API


def RunSteps(api: DEPS):
  # Option flags cannot be created with filter flag but no filter delimiter
  with api.assertions.assertRaises(ValueError) as caught:
    steps.TestOptionFlags.create(filter_flag='--filter-flag')
  api.assertions.assertEqual(
    str(caught.exception), "'filter_delimiter' must be set if 'filter_flag' is"
  )

  # Options for running with no tests to retry should equal the input options
  options = steps.TestOptions.create()
  new_options = options.for_running('suffix', [])
  api.assertions.assertEqual(new_options, options)

  # Options for running a large number of tests to retry should equal
  # the input options
  options = steps.TestOptions.create()
  new_options = options.for_running(
    'suffix', ['test{}'.format(x) for x in range(200)]
  )
  api.assertions.assertEqual(new_options, options)

  # Options for running a small number of tests for 'without patch'
  # suffix will be modified
  options = steps.TestOptions.create()
  new_options = options.for_running('without patch', ['test0'])
  api.assertions.assertEqual(
    new_options,
    steps.TestOptions.create(
      repeat_count=steps.REPEAT_COUNT_FOR_FAILING_TESTS,
      retry_limit=0,
      force_independent_tests=True,
    ),
  )

  # Options for running a small number of tests for other suffixes should equal
  # the input options
  options = steps.TestOptions.create()
  new_options = options.for_running('suffix', ['test0'])
  api.assertions.assertEqual(new_options, options)

  # Options with non-None repeat count for running a small number of tests for
  # 'without patch' suffix should equal the input options
  options = steps.TestOptions.create(
    repeat_count=3,
  )
  new_options = options.for_running('without patch', ['test0'])
  api.assertions.assertEqual(new_options, options)

  # Options for CI 'retry shards' suffix will be modified.
  options = steps.TestOptions.create()
  new_options = options.for_running('retry shards', ['test0'])
  api.assertions.assertEqual(
    new_options,
    steps.TestOptions.create(
      retry_limit=steps.RETRY_LIMIT_FOR_CI_RETRY_SHARDS,
    ),
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'full',
    api.post_process(post_process.DropExpectation),
  )
