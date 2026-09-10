# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Presubmit for flaky_reproducer unittests.

These tests execute to
"""

from __future__ import annotations


PRESUBMIT_VERSION = '2.0.0'

USE_PYTHON3 = True


def CheckFlakyReproducerLibsUnittestsWork(input_api, output_api):
  return input_api.RunTests(
    input_api.canned_checks.GetUnitTests(
      input_api,
      output_api,
      ['resources/unittests/run.py'],
      run_on_python2=False,
      run_on_python3=True,
    ),
    parallel=True,
  )
