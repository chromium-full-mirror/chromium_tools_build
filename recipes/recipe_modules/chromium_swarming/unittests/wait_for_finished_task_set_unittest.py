#!/usr/bin/env vpython
# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import mock

THIS_DIR = os.path.dirname(__file__)

sys.path.insert(0, os.path.abspath(os.path.join(THIS_DIR, '..', 'resources')))
import wait_for_finished_task_set


class TasksToCollectTest(unittest.TestCase):
  def test_swarming_url(self):
    tasks = wait_for_finished_task_set.TasksToCollect([[]])
    self.assertEqual(tasks.swarming_prpc_json([]), '{"task_id": []}')
    # Directly assign to the attribute to bypass the property calculation.
    # Add an unsorted array to make sure the url itself gets sorted.
    tasks = wait_for_finished_task_set.TasksToCollect([['b', 'a', 'c']])
    self.assertEqual(
      tasks.swarming_prpc_json(['a', 'b', 'c']), '{"task_id": ["a", "b", "c"]}'
    )

  def test_empty(self):
    tasks = wait_for_finished_task_set.TasksToCollect([[]])
    self.assertEqual(tasks.unfinished_tasks, [])
    self.assertEqual(tasks.finished_task_sets, [])

  def test_one_set(self):
    tasks = wait_for_finished_task_set.TasksToCollect([['a', 'b', 'c']])
    self.assertEqual(tasks.unfinished_tasks, ['a', 'b', 'c'])
    self.assertEqual(tasks.finished_task_sets, [])

    tasks.process_result({'states': ['PENDING', 'PENDING', 'COMPLETED']}, 3)
    self.assertEqual(tasks.unfinished_tasks, ['a', 'b'])
    self.assertEqual(tasks.finished_task_sets, [])

    tasks.process_result({'states': ['COMPLETED', 'COMPLETED']}, 2)
    self.assertEqual(tasks.unfinished_tasks, [])
    self.assertEqual(tasks.finished_task_sets, [['a', 'b', 'c']])

  def test_many_tasks(self):
    with mock.patch.object(wait_for_finished_task_set, 'TASK_BATCH_SIZE', 4):
      tasks = wait_for_finished_task_set.TasksToCollect(
        [[str(i) + 'task' for i in range(11)]]
      )
      self.assertEqual(
        tasks.task_batches,
        # 10task is in the first list because the list of tasks is sorted.
        [
          ['0task', '10task', '1task', '2task'],
          ['3task', '4task', '5task', '6task'],
          ['7task', '8task', '9task'],
        ],
      )

  def test_multiple_sets(self):
    tasks = wait_for_finished_task_set.TasksToCollect(
      [
        ['a', 'b', 'c'],
        ['d'],
        ['e', 'f'],
      ]
    )

    self.assertEqual(tasks.unfinished_tasks, ['a', 'b', 'c', 'd', 'e', 'f'])
    self.assertEqual(tasks.finished_task_sets, [])

    tasks.process_result(
      {
        'states': [
          # Set 1
          'PENDING',
          'COMPLETED',
          'PENDING',
          # Set 2
          'COMPLETED',
          # Set 3
          'PENDING',
          'RUNNING',
        ]
      },
      6,
    )
    self.assertEqual(tasks.unfinished_tasks, ['a', 'c', 'e', 'f'])
    self.assertEqual(tasks.finished_task_sets, [['d']])

    tasks.process_result(
      {
        'states': [
          # Set 1
          'PENDING',
          'COMPLETED',
          # Set 2
          'COMPLETED',
          'COMPLETED',
        ]
      },
      4,
    )
    self.assertEqual(tasks.unfinished_tasks, ['a'])
    self.assertEqual(tasks.finished_task_sets, [['d'], ['e', 'f']])

    tasks.process_result({'states': ['COMPLETED']}, 1)
    self.assertEqual(tasks.unfinished_tasks, [])
    self.assertEqual(
      tasks.finished_task_sets, [['a', 'b', 'c'], ['d'], ['e', 'f']]
    )


class WaitForFinishedTaskSetTest(unittest.TestCase):
  @mock.patch.object(wait_for_finished_task_set, 'real_main')
  def test_main(self, real_main):
    """Tests the real main, to make sure there aren't small syntax errors."""
    real_main.return_value = (127, None)
    fd, fname = tempfile.mkstemp()
    os.close(fd)
    try:
      with open(fname, 'w') as f:
        json.dump([['foo']], f)

      self.assertEqual(
        wait_for_finished_task_set.main(
          [
            None,
            '--swarming-server',
            'blah',
            '--swarming-py-path',
            '/path/to/swarming.py',
            '--output-json',
            '/path/to/out.json',
            '--input-json',
            fname,
          ]
        ),
        127,
      )
    finally:
      if os.path.exists(fname):
        os.unlink(fname)

  class FakeProcess:
    def __init__(self, out, err=None, returncode=0):
      self._out = out
      self._err = err
      self.returncode = returncode

    def communicate(self, _input_data):
      return self._out, self._err

  @mock.patch.object(subprocess, 'Popen')
  @mock.patch.object(time, 'sleep')
  def test_integration(self, sleep_mock, popen_mock):
    popen_mock.return_value = self.FakeProcess(
      json.dumps({'states': ['COMPLETED', 'COMPLETED']})
    )
    retcode, out_json = wait_for_finished_task_set.real_main(
      wait_for_finished_task_set.TasksToCollect(
        [
          ['a', 'b'],
        ]
      ),
      3,
      'https://swarming-server',
    )
    self.assertEqual(retcode, 0)
    self.assertEqual(out_json, {'attempts': 3, 'sets': [['a', 'b']]})

    # Shouldn't ever be called, just here so that if it gets called accidentally
    # the tests don't take forever.
    sleep_mock.assert_not_called()

  @mock.patch.object(time, 'sleep')
  @mock.patch.object(subprocess, 'Popen')
  def test_integration_sleep(self, popen_mock, sleep_mock):
    popen_mock.return_value = self.FakeProcess(
      json.dumps({'states': ['COMPLETED', 'COMPLETED']})
    )
    tasks = wait_for_finished_task_set.TasksToCollect(
      [
        ['a', 'b'],
      ]
    )
    tasks.process_result = mock.MagicMock()
    num_calls = [0]

    def side_effect(_, __):
      if num_calls[0] > 8:
        tasks.finished_tasks.add('a')
        tasks.finished_tasks.add('b')
      num_calls[0] += 1

    tasks.process_result.side_effect = side_effect

    retcode, out_json = wait_for_finished_task_set.real_main(
      tasks, 0, 'https://swarming-server'
    )
    self.assertEqual(retcode, 0)
    self.assertEqual(out_json, {'attempts': 9, 'sets': [['a', 'b']]})

    self.assertEqual(
      sleep_mock.mock_calls,
      [
        mock.call(2),
        mock.call(4),
        mock.call(8),
        mock.call(15),
        mock.call(15),
        mock.call(15),
        mock.call(15),
        mock.call(15),
        mock.call(15),
      ],
    )


if __name__ == '__main__':
  unittest.main()
