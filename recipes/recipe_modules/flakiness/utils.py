# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr
import copy
import re  # noqa: F401

from RECIPE_MODULES.build.chromium_tests import steps


############################### HELPER FUNCTIONS ###############################


def get_base_test_name(test_name):
  """Strips PRE_ prefixes to return the base test name."""
  base_name = test_name
  while '.PRE_' in base_name:
    base_name = base_name.replace('.PRE_', '.', 1)
  return base_name


def get_actual_test_group(test_name, all_suite_test_names):
  """Returns all tests in the suite that belong to the same group (base test and its PRE_ variants)."""
  base_name = get_base_test_name(test_name)
  if '.' not in base_name:
    return [test_name]

  return [t for t in all_suite_test_names if get_base_test_name(t) == base_name]


# The longest filter delimiter ('::' for isolated script tests).
_MAX_FILTER_DELIMITER_LENGTH = 2


def get_filter_char_limit(test, host_is_win):
  """Returns the max length of the test filter for the test.

  Args:
    test: The steps.Test object.
    host_is_win: Whether the bot that runs the recipe is a Windows bot.
      Local tests (for example, ScriptTest) run on this bot.
  """
  # A test that runs on swarming (also when wrapped in a TestWrapper, for
  # example ExperimentalTest) uses the OS of the swarming bot. Any other test
  # runs on the host bot.
  if test.runs_on_swarming:
    is_win = test.dispatches_to_windows()
  else:
    is_win = host_is_win
  if is_win:
    return steps.WINDOWS_FILTER_CHAR_LIMIT
  return steps.DEFAULT_FILTER_CHAR_LIMIT


def get_filter_length(test_names):
  """Returns the max length of a filter string that joins test_names."""
  return sum(len(t) + _MAX_FILTER_DELIMITER_LENGTH for t in test_names)


def apply_script_test_filter(test, test_filter, repeat_count):
  script_args = list(
    [
      '--gtest_repeat=%s' % str(repeat_count),
      '--gtest_filter=%s' % str(':'.join(test_filter)),
      '--shards=1',
    ]
  )
  test.spec = attr.evolve(test.spec, script_args=script_args)
  return test


def apply_default_test_filter(test, test_filter, repeat_count):
  test_copy = copy.copy(test)
  options = steps.TestOptions.create(
    test_filter=test_filter, repeat_count=repeat_count, retry_limit=0
  )
  test_copy.test_options = options
  return test_copy


def apply_swarming_shard_test_filter(test, test_filter, shard_runs):
  test_copy = copy.copy(test)
  options = steps.TestOptions.create(
    test_filter=test_filter, repeat_count=shard_runs, retry_limit=0
  )
  test_copy.test_options = options
  # we don't use swarming's shard mechanism for endorser runs.
  test_copy.spec = test.spec.with_shards(1)
  return test_copy
