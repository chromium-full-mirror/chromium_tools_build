# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import unittest

from libs.test_binary import create_test_binary_from_jsonish
from libs.result_summary import create_result_summary_from_output_json
from libs.strategies.base_strategy import BaseStrategy
from testdata import get_test_data


class BaseStrategyTest(unittest.TestCase):
  def setUp(self):
    test_binary = create_test_binary_from_jsonish(
      json.loads(get_test_data('gtest_test_binary.json'))
    )
    result_summary = create_result_summary_from_output_json(
      json.loads(get_test_data('gtest_good_output.json'))
    )
    self.strategy = BaseStrategy(
      test_binary, result_summary, 'MockUnitTests.FailTest'
    )

  def test_run(self):
    self.assertTrue(self.strategy.valid_for_test())
    with self.assertRaises(NotImplementedError):
      self.strategy.run()

  def test_reproducing_step(self):
    reproducing_step = self.strategy._reproducing_step(
      self.strategy.test_binary
    )
    self.assertEqual(reproducing_step.strategy, self.strategy.name)
