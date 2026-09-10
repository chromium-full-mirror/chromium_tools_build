# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re

from .base_test_binary import (
  BaseTestBinary,
  TestBinaryWithBatchMixin,
  TestBinaryWithParallelMixin,
)
from .gtest_test_binary import GTestTestBinary
from .blink_web_tests_binary import BlinkWebTestsBinary

TEST_BINARIES = {
  GTestTestBinary.__name__: GTestTestBinary,
  BlinkWebTestsBinary.__name__: BlinkWebTestsBinary,
}

NOT_SUPPORTED_TEST_SUITES = ('chrome_all_tast_tests',)


class TaskRequest:
  '''Simplified swarming.TaskRequest reader for use without recipe repo.

  Check https://source.chromium.org/chromium/infra/infra/+/main:recipes-py/recipe_modules/swarming/api.py
  for implement detail and schema.
  '''

  def __init__(self, tags, task_slices):
    self.tags = tags
    self.task_slices = task_slices

  @staticmethod
  def from_jsonish(d):
    return TaskRequest(
      d.get('tags', ()),
      [
        TaskRequest.TaskSlice.from_jsonish(ts)
        for ts in d.get('task_slices', ())
      ],
    )

  def __getitem__(self, it):
    return self.task_slices[it]

  def __len__(self):
    return len(self.task_slices)

  class TaskSlice:
    def __init__(self):
      self.command = []
      self.relative_cwd = None
      self.env_vars = dict()
      self.dimensions = dict()
      self.cas_input_root = None

    @staticmethod
    def from_jsonish(d):
      def kv_list_to_dict(kv_list):
        ret = {}
        for kv in kv_list:
          ret[kv['key']] = kv.get('value', None)
        return ret

      p = d.get('properties', {})
      obj = TaskRequest.TaskSlice()
      obj.command = p.get('command', [])
      obj.relative_cwd = p.get('relative_cwd', '')
      obj.env_vars = kv_list_to_dict(p.get('env_vars', []))
      obj.dimensions = kv_list_to_dict(p.get('dimensions', []))
      if 'cas_input_root' in p:
        digest = p['cas_input_root']['digest']
        obj.cas_input_root = (
          digest['hash'] + '/' + digest.get('size_bytes', '0')
        )
      return obj


def create_test_binary_from_task_request(task_request):
  """Factory method for TestBinary(s) that distinguish and create the correct
  TestBinary object."""
  if len(task_request) < 1:
    raise ValueError("No TaskSlice found in the TaskRequest.")

  # Raise NotImplementedError for known test suites that not actually a GTest.
  test_suite = None
  for tag in task_request.tags:
    if tag.startswith('test_suite:'):
      test_suite = tag[len('test_suite:') :]
  if test_suite in NOT_SUPPORTED_TEST_SUITES:
    raise NotImplementedError('Not Supported test suite: %s' % test_suite)

  request_slice = task_request[-1]
  command = ' '.join(request_slice.command)
  if re.search('result_adapter(.exe)? gtest', command):
    return GTestTestBinary.from_task_request(task_request)
  # android device tests (e.g. android-12-x64-rel) are not using
  # result_adapter, detecting --test-launcher-summary-output as a workaround
  # as it doesn't exists in blink_web_tests.
  if '--test-launcher-summary-output' in command:
    return GTestTestBinary.from_task_request(task_request)
  if '--write-run-histories-to=' in command:
    return BlinkWebTestsBinary.from_task_request(task_request)
  raise NotImplementedError('Not Supported test binary: %s' % command)


def create_test_binary_from_jsonish(json_data):
  """Gets test binary object from a jsonish"""
  if not 'class_name' in json_data:
    raise ValueError('Invalid TestBinary json format, missing class_name.')
  if json_data['class_name'] not in TEST_BINARIES:
    raise ValueError(
      'Unknown TestBinary class name: {0}.'.format(json_data['class_name'])
    )
  return TEST_BINARIES[json_data['class_name']].from_jsonish(json_data)
