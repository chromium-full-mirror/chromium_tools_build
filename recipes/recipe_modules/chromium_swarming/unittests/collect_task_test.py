#!/usr/bin/env vpython
# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

import mock

THIS_DIR = os.path.dirname(__file__)

RESOURCES_DIR = os.path.abspath(os.path.join(THIS_DIR, '..', 'resources'))
sys.path.insert(0, RESOURCES_DIR)
import collect_task


class CollectTaskTest(unittest.TestCase):

  def setUp(self):
    super().setUp()

    self.subprocess_calls = []
    def mocked_subprocess_call(args, **_):
      self.subprocess_calls.append(args)
      return 0

    def mocked_subprocess_popen(args, **_):
      self.subprocess_calls.append(args)

      class FakeProcess:

        def __init__(self):
          self.returncode = 0
        def poll(self):
          return True

      return FakeProcess()
    m = mock.patch('subprocess.call', side_effect=mocked_subprocess_call)
    m.start()
    self.addCleanup(m.stop)
    p = mock.patch('subprocess.Popen', side_effect=mocked_subprocess_popen)
    p.start()
    self.addCleanup(p.stop)

    self.temp_dir = tempfile.mkdtemp()
    self.merge_script_log = os.path.join(
        self.temp_dir, 'merge_script_log.txt')

  def tearDown(self):
    shutil.rmtree(self.temp_dir)
    super().tearDown()

  def test_basic(self):
    collect_cmd = [
      'swarming.py',
      'positional0',
      '--swarming-arg0', '0',
      '--swarming-arg1', '1',
      'positional1',
    ]
    build_props_json = os.path.join(self.temp_dir, 'build_properties.json')
    task_output_dir = os.path.join(self.temp_dir, 'task_output_dir')
    os.makedirs(task_output_dir)
    summary_json = os.path.join(task_output_dir, 'summary.json')
    output_json = os.path.join(self.temp_dir, 'output.json')
    exit_code = collect_task.collect_task(
        collect_cmd, 'merge.py', self.merge_script_log,
        build_props_json, None, task_output_dir, output_json,
        summary_json)
    self.assertEqual(0, exit_code)

    # Should append correct --task-output-dir to args after '--'.
    self.assertEqual([
        [
            'swarming.py',
            'positional0',
            '--swarming-arg0',
            '0',
            '--swarming-arg1',
            '1',
            'positional1',
            '-output-dir',
            task_output_dir,
            '-task-summary-json',
            summary_json,
        ],
        [
            'vpython3',
            'merge.py',
            '--build-properties',
            build_props_json,
            '--task-output-dir',
            task_output_dir,
            '-o',
            output_json,
        ],
    ], self.subprocess_calls)

  def test_task_output_dir_handling(self):
    collect_cmd = [
      'swarming.py',
      'positional0',
      '--swarming-arg0', '0',
      '--swarming-arg1', '1',
      'positional1',
    ]
    merge_script = os.path.join(RESOURCES_DIR, 'noop_merge.py')
    task_output_dir = os.path.join(self.temp_dir, 'task_output_dir')
    os.makedirs(task_output_dir)
    summary_json = os.path.join(task_output_dir, 'summary.json')
    with open(summary_json, 'w') as f:
      f.write('{}')

    shard0_output_dir = os.path.join(task_output_dir, '0')
    os.makedirs(shard0_output_dir)
    shard0_output_json = os.path.join(shard0_output_dir, 'output.json')
    with open(shard0_output_json, 'w') as f:
      f.write('{}')

    build_props_json = os.path.join(self.temp_dir, 'build_properties.json')
    output_json = os.path.join(self.temp_dir, 'output.json')
    exit_code = collect_task.collect_task(
        collect_cmd, merge_script, self.merge_script_log,
        build_props_json, None, task_output_dir, output_json,
        summary_json)

    self.assertEquals(0, exit_code)
    self.assertEquals([
        [
            'swarming.py',
            'positional0',
            '--swarming-arg0',
            '0',
            '--swarming-arg1',
            '1',
            'positional1',
            '-output-dir',
            task_output_dir,
            '-task-summary-json',
            summary_json,
        ],
        [
            'vpython3',
            merge_script,
            '--build-properties',
            build_props_json,
            '--summary-json',
            summary_json,
            '--task-output-dir',
            task_output_dir,
            '-o',
            output_json,
            shard0_output_json,
        ],
    ], self.subprocess_calls)

  def test_custom_merge(self):
    collect_cmd = [
      'swarming.py',
      'positional0',
      '--swarming-arg0', '0',
      '--swarming-arg1', '1',
      'positional1',
    ]
    merge_script = os.path.join(self.temp_dir, 'fake_custom_merge.py')
    task_output_dir = os.path.join(self.temp_dir, 'task_output_dir')
    os.makedirs(task_output_dir)
    summary_json = os.path.join(task_output_dir, 'summary.json')

    build_props_json = os.path.join(self.temp_dir, 'build_properties.json')
    output_json = os.path.join(self.temp_dir, 'output.json')
    exit_code = collect_task.collect_task(
        collect_cmd, merge_script, self.merge_script_log,
        build_props_json, None, task_output_dir, output_json,
        summary_json)

    self.assertEquals(0, exit_code)
    self.assertEquals([
        [
            'swarming.py',
            'positional0',
            '--swarming-arg0',
            '0',
            '--swarming-arg1',
            '1',
            'positional1',
            '-output-dir',
            task_output_dir,
            '-task-summary-json',
            summary_json,
        ],
        [
            'vpython3',
            merge_script,
            '--build-properties',
            build_props_json,
            '--task-output-dir',
            task_output_dir,
            '-o',
            output_json,
        ],
    ], self.subprocess_calls)

  def test_custom_merge_with_args(self):
    collect_cmd = [
      'swarming.py',
      'positional0',
      '--swarming-arg0', '0',
      '--swarming-arg1', '1',
      'positional1',
    ]
    merge_script = os.path.join(self.temp_dir, 'fake_custom_merge.py')
    task_output_dir = os.path.join(self.temp_dir, 'task_output_dir')
    os.makedirs(task_output_dir)
    summary_json = os.path.join(task_output_dir, 'summary.json')

    build_props = json.dumps({
      'sample_build_property': 'sample_value'
    })
    merge_args = json.dumps([
      '--merge-arg0', 'merge-arg0-value',
    ])

    output_json = os.path.join(self.temp_dir, 'output.json')
    exit_code = collect_task.collect_task(
        collect_cmd, merge_script, self.merge_script_log, build_props,
        merge_args, task_output_dir, output_json, summary_json)

    self.assertEquals(0, exit_code)
    self.assertEquals([
        [
            'swarming.py',
            'positional0',
            '--swarming-arg0',
            '0',
            '--swarming-arg1',
            '1',
            'positional1',
            '-output-dir',
            task_output_dir,
            '-task-summary-json',
            summary_json,
        ],
        [
            'vpython3',
            merge_script,
            '--build-properties',
            build_props,
            '--task-output-dir',
            task_output_dir,
            '--merge-arg0',
            'merge-arg0-value',
            '-o',
            output_json,
        ],
    ], self.subprocess_calls)

  def test_empty_output_json(self):
    collect_cmd = [
      'swarming.py',
      'positional0',
      '--swarming-arg0', '0',
      '--swarming-arg1', '1',
      'positional1',
    ]
    build_props_json = os.path.join(self.temp_dir, 'build_properties.json')
    task_output_dir = os.path.join(self.temp_dir, 'task_output_dir')
    os.makedirs(task_output_dir)
    summary_json = os.path.join(task_output_dir, 'summary.json')

    shard0_dir = os.path.join(task_output_dir, '0')
    os.makedirs(shard0_dir)
    extant_shard_json = os.path.join(shard0_dir, 'output.json')
    with open(extant_shard_json, 'w') as f:
      f.write('{}')

    shard1_dir = os.path.join(task_output_dir, '1')
    os.makedirs(shard1_dir)
    with open(os.path.join(shard1_dir, 'output.json'), 'w'):
      # Intentionally leave 1/output.json empty.
      pass

    output_json = os.path.join(self.temp_dir, 'output.json')
    exit_code = collect_task.collect_task(
        collect_cmd, 'merge.py', self.merge_script_log, build_props_json, None,
        task_output_dir, output_json, summary_json)
    self.assertEqual(0, exit_code)

    # Should append correct --task-output-dir to args after '--'.
    self.assertEqual([
        [
            'swarming.py',
            'positional0',
            '--swarming-arg0',
            '0',
            '--swarming-arg1',
            '1',
            'positional1',
            '-output-dir',
            task_output_dir,
            '-task-summary-json',
            summary_json,
        ],
        [
            'vpython3',
            'merge.py',
            '--build-properties',
            build_props_json,
            '--task-output-dir',
            task_output_dir,
            '-o',
            output_json,
            extant_shard_json,
        ],
    ], self.subprocess_calls)


if __name__ == '__main__':
  unittest.main()
