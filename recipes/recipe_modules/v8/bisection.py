# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import bisect


def keyed_bisect(git_range, is_bad):
  """Wrapper for using python's bisection with a generic key function.

  Args:
    git_range: List with the keys for bisection. Sorted in the order
           good -> bad. It's assumed that git_range[-1] is a "bad" revision and
           that the revision before git_range[0] is "good".
    is_bad: Callable that takes a key and returns a boolean indicating
            whether it's good or bad.
  """

  class LazyMap:
    def __getitem__(self, i):
      # The function is assumed to return False for good keys and True for bad
      # ones. By initializing bisect with True below, bisection handles the two
      # cases (1) False < True for good keys and (2) True >= True for bad keys.
      return bool(is_bad(git_range[i]))

  # Initialize with len(git_range) - 1 to omit retesting git_range[-1] as we
  # assume it's bad.
  return git_range[bisect.bisect_left(LazyMap(), True, 0, len(git_range) - 1)]
