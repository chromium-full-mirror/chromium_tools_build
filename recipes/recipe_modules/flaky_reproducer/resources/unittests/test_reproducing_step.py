# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import unittest

from libs import ReproducingStep
from testdata import get_test_data


class ReproducingStepTest(unittest.TestCase):
  def test_from_jsonish(self):
    json_data = json.loads(get_test_data('reproducing_step.json'))
    step = ReproducingStep.from_jsonish(json_data)
    self.assertEqual(step.reproducing_rate, 0.9)
    self.assertEqual(step.duration, 123)
    self.assertEqual(step.reproduced_cnt, 1)
    self.assertEqual(step.total_run_cnt, 30)
    self.assertTrue(step)
    return step

  def test_to_jsonish(self):
    self.maxDiff = None
    step = self.test_from_jsonish()
    json_data = json.loads(get_test_data('reproducing_step.json'))
    self.assertDictEqual(step.to_jsonish(), json_data)

  def test_readable_info(self):
    self.maxDiff = None
    step = self.test_from_jsonish()
    self.assertEqual(
      step.readable_info(),
      (
        'The failure could be reproduced (90.0%) with command'
        ' by repeat strategy:\n\n'
        'vpython3 ../../testing/test_env.py ./base_unittests.exe'
        ' --test-launcher-bot-mode --asan=0 --lsan=0 --msan=0 --tsan=0'
        ' --cfi-diag=0 --test-launcher-retry-limit=0 '
        '--gtest_filter=MockUnitTests.CrashTest:MockUnitTests.PassTest '
        '--gtest_repeat=1 '
        '--test-launcher-batch-limit=0 --test-launcher-jobs=5'
      ),
    )

  def test_readable_info_not_reproduced(self):
    self.maxDiff = None
    step = self.test_from_jsonish()
    step.reproducing_rate = 0
    self.assertEqual(
      step.readable_info(),
      'This failure was NOT reproduced by repeat strategy.',
    )

  def test_better_than(self):
    step = self.test_from_jsonish()
    test_binary = step.test_binary

    step_0 = ReproducingStep(
      test_binary,
      'test',
      0,
    )
    step_50 = ReproducingStep(test_binary, 'test', 0.5, 123)
    step_60 = ReproducingStep(test_binary, 'test', 0.6, 123)
    step_90 = ReproducingStep(test_binary.with_tests(['a']), 'test', 0.9, 12)
    step_92 = ReproducingStep(test_binary, 'test', 0.92, 123)
    step_95 = ReproducingStep(test_binary, 'test', 0.95, 123)
    self.assertFalse(step_0)
    self.assertTrue(step_50.better_than(step_0))
    self.assertTrue(step_60.better_than(step_50))
    self.assertTrue(step_90.better_than(step_60))
    self.assertTrue(step_90.better_than(step_92))
    self.assertTrue(step_95.better_than(step_90))

  def test_better_than_reproducing_cnt(self):
    step_0 = ReproducingStep(None, 'test', 0, 0, 5)
    step_50 = ReproducingStep(None, 'test', 0.5, 123, 7)
    step_60 = ReproducingStep(None, 'test', 0.6, 123, 1)
    self.assertTrue(step_50.better_than(step_0))
    self.assertTrue(step_50.better_than(step_60))
    self.assertTrue(step_60.better_than(step_0))
