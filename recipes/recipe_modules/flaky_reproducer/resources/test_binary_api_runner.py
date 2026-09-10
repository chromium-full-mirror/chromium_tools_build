# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Exporting TestBinary object methods as CLI for recipe scripts."""

from __future__ import annotations

import sys

from api_runner_common import main
from libs.test_binary import (
  create_test_binary_from_task_request,
  BaseTestBinary,
  TaskRequest,
)
from libs.strategies import ReproducingStep


def method_create_test_binary_from_task_request(task_request: TaskRequest):
  """Factory method for TestBinary(s) that distinguish and create the correct
  TestBinary object."""
  test_binary = create_test_binary_from_task_request(task_request)
  test_binary = test_binary.strip_for_bots()
  return test_binary.to_jsonish()


def get_test_binary_swarming_task_config(test_binary: BaseTestBinary):
  """Get task request configs used to launch test binary in swarming"""
  return {
    'cas_input_root': test_binary.cas_input_root,
    'cwd': test_binary.cwd,
    'dimensions': test_binary.dimensions,
    'env_vars': test_binary.env_vars,
  }


def update_test_binary_with_reproducing_step(
  test_binary: BaseTestBinary, reproducing_step: ReproducingStep
):
  """Apply reproducing step to test binary."""
  return test_binary.with_options_from_other(
    reproducing_step.test_binary
  ).to_jsonish()


def test_binary_as_command(test_binary: BaseTestBinary, output: str):
  """Return a executable command line."""
  return test_binary.as_command(output)


methods = {
  'create_test_binary_from_task_request': method_create_test_binary_from_task_request,
  'get_test_binary_swarming_task_config': get_test_binary_swarming_task_config,
  'update_test_binary_with_reproducing_step': update_test_binary_with_reproducing_step,
  'test_binary_as_command': test_binary_as_command,
}

if __name__ == '__main__':
  main(sys.argv[1:], methods)  # pragma: no cover
