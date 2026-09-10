# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr
import copy
import re

from RECIPE_MODULES.build.chromium_tests import steps


# TODO (crbug/1456545) - delete this class once it's not in use.
class TestDefinition:
  """A class to contain ResultDB TestReuslt Proto information.

  Test ID, variant hash (see go/resultdb-concepts) and whether the test comes
  from an experimental suite distinguish a |TestDefinition|. This is achieved by
  overriding __eq__ and __hash__ methods.

  Attributes:
    * duration_milliseconds: (int) Test duration in milliseconds.
    * test_id: (str) ResultDB's test_id (go/resultdb-concepts)
    * test_object: (steps.AbstractTest) The test object where this test
      comes from.
    * variant_hash: (str) ResultDB's variant_hash (go/resultdb-concepts)
    * file_path: (str) path to the test, defined through ResultDB's
                 test_metadata.location.file_loc. See proto at
                 https://source.chromium.org/chromium/infra/infra/+/main:
                 go/src/go.chromium.org/luci/resultdb/proto/v1/test_result.proto
  """

  def __init__(
    self,
    test_id,
    test_name=None,
    duration_milliseconds=None,
    test_object=None,
    variant_hash=None,
    file_path=None,
  ):
    """
    Args:
      * test_id: (str) ResultDB test id
      * test_name: (str) Test name to input to test suites.
      * duration_milliseconds: (int) Test duration in milliseconds.
      * test_object: (steps.AbstractTest) The test object where this
        test comes from.
      * variant_hash: (str) ResultDB's variant hash
      * file_path: (str) path to the test, defined through ResultDB's
        test_metadata.location.file_loc. See proto at
        https://source.chromium.org/chromium/infra/infra/+/main:
        go/src/go.chromium.org/luci/resultdb/proto/v1/test_result.proto
    """
    self.test_id = test_id
    self.test_name = test_name
    self.duration_milliseconds = duration_milliseconds
    self.variant_hash = variant_hash
    self.test_object = test_object
    self.file_path = file_path

  def __eq__(self, t2):
    return (self.test_id, self.variant_hash) == t2

  def __hash__(self):
    return hash((self.test_id, self.variant_hash))


############################### HELPER FUNCTIONS ###############################


def set_to_string(test_set):
  """Joins set of test_id, variant hash tuples into strings.

  For step presentations, test tuple sets cannot be logged so it is
  necessary and preferred to convert to lists of sorted concatenated
  strings.
  """
  return sorted(
    [
      '_'.join([test_result.test_id, test_result.variant_hash])
      for test_result in test_set
    ]
  )


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
