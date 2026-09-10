# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Common logic for interacting with the canonical test results format.

This is expected to be shared between multiple parts of the test_utils
module and exposed to other modules.

The canonical format is a dict of the form:

  {
    'valid': bool,
    # Can be any iterable.
    'failures': [
      'failed_test1',
      ...
    ],
    'total_tests_ran': int,
    'pass_fail_counts': {
      'test1': {
        'PASS_COUNT': int,
        'FAIL_COUNT': int,
      },
      ...
    },
    'findit_notrun': set(),
  }
"""

from __future__ import annotations


def result_format(
  valid=False,
  failures=None,
  total_tests_ran=0,
  pass_fail_counts=None,
  findit_notrun=None,
):
  """Returns a dict in the canonical format with all required keys.

  All of the arguments below will be present in the returned dict, whether
  they were explicitly passed as arguments or not.

  Args:
    valid: A boolean indicating whether the test run was valid.
    failures: An iterable of strings, where each string is the name of a test
      that failed.
    total_tests_ran: An integer number of tests that were run.
    pass_fail_counts: A dictionary that maps test names to pass and fail counts,
      e.g.
        {
          'test3': { 'PASS_COUNT': 3, 'FAIL_COUNT': 2 }
        }
    findit_notrun: A temporary field for FindIt. Lists tests for which every
      test run had result NOTRUN or UNKNOWN.
  """
  return {
    'valid': valid,
    'failures': failures or [],
    'total_tests_ran': total_tests_ran,
    'pass_fail_counts': pass_fail_counts or {},
    'findit_notrun': findit_notrun or set(),
  }


def deterministic_failures(canonical_result):
  """Return a list of tests that consistently failed.

  Args:
    canonical_result: A dict in the canonical format,
      as would be returned by result_format above.
  """
  failures = []
  for test_name, result in canonical_result['pass_fail_counts'].items():
    success_count = result['pass_count']
    fail_count = result['fail_count']
    if fail_count > 0 and success_count == 0:
      failures.append(test_name)
  return failures
