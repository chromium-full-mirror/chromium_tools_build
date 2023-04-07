# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Execute the unittests as recipe tests.

This module sharing the code that runs as part of recipe and on swarming bot.
That some tests are implemented as unitests to cover 100% of the lines.
"""

DEPS = []


def RunSteps(api):
  # If these modules are imported at top level, the recipes lint check fails
  # pylint: disable=import-outside-toplevel
  import os
  import unittest

  test_loader = unittest.TestLoader()
  this_dir = os.path.dirname(os.path.abspath(__file__))
  top_level_dir = os.path.join(this_dir, os.pardir)
  unittest_dir = os.path.join(top_level_dir, 'unittests')
  test_suite = test_loader.discover(unittest_dir, top_level_dir=top_level_dir)
  test_suite.debug()


from recipe_engine.post_process import DropExpectation


def GenTests(api):
  yield api.test(
      'unittests',
      api.post_process(DropExpectation),
  )
