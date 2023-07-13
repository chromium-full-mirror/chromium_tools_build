# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from typing import Tuple


VersionTuple = Tuple[int, int, int, int]


def normalize_version(version) -> VersionTuple:
  """Accept multiple input types to represent a version, and return a normalized
  version tuple.

  Supported input types:
    * Tuple of integers of various lengths, e.g. (12, ) (12, 5), (12, 5, 1, 9)
    * Dot-separated string of various length, e.g. '12', '12.5', '12.5.1.9'
  """
  if isinstance(version, str):
    version = tuple(version.split('.'))

  assert isinstance(version, tuple), f'Expected a tuple, found {type(version)}.'

  version = tuple(int(c) for c in version)

  return (version + (0, ) * 3)[:4]
