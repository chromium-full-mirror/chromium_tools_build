# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'chromium_tests',
    'recipe_engine/properties',
    'recipe_engine/step',
]

from recipe_engine.post_process import (DropExpectation)
from RECIPE_MODULES.build.chromium_tests import steps


def RunSteps(api):
  test_spec = steps.MockTestSpec.create(
      name=api.properties.get('test_name', 'MockTest'))
  test_spec.get_test(api.chromium_tests)


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(DropExpectation),
  )
