# Copyright 2023 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from recipe_engine import post_process
from recipe_engine.recipe_api import Property
from RECIPE_MODULES.build.chromium_tests import steps

DEPS = [
    'recipe_engine/assertions',
    'recipe_engine/properties',
    "recipe_engine/resultdb",
    "recipe_engine/step",
    'chromium_tests',
    'test_utils',
]

PROPERTIES = {
    'per_suffix_valid': Property(default={}),
    'per_suffix_failures': Property(default={}),
    'suffix': Property(default='')
}

from recipe_engine.recipe_api import Property
from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps


def RunSteps(api, per_suffix_valid, per_suffix_failures, suffix):
  test_spec = steps.MockTestSpec.create(
      name='browser_tests',
      per_suffix_failures=per_suffix_failures,
      per_suffix_valid=per_suffix_valid)
  test = test_spec.get_test(api.chromium_tests)
  test.failures_including_retry('with patch', api)


def GenTests(api):
  yield api.test(
      'basic',
      api.properties(
          suffix='with patch',
          per_suffix_valid={
              'with patch': True,
              'retry shards with patch': True,
          },
          per_suffix_failures={
              'with patch': ['testA'],
              'retry shards with patch': ['testA', 'testB'],
          },
      ),
      api.post_process(post_process.MustRun,
                       'new failures in retry step for suite browser_tests'),
      api.post_process(post_process.DropExpectation),
  )
