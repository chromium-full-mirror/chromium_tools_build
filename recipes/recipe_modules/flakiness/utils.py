# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr
import copy
import re

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
