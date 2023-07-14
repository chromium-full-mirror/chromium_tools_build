# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re

from typing import Tuple

REF_LINE_RE = re.compile(
    r'refs\/tags\/(\d+(?:\.\d+){2,3})-pgo\ ([0-9a-f]{40})')

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

  assert 0 < len(version) <= 4

  version = tuple(int(c) for c in version)

  return (version + (0, ) * 3)[:4]


def choose_revision_to_roll(ref_lines, last_version):
  """Choose the next V8 revision to roll based on recent tags.

  Args:
    ref_lines: List ref strings in the format "%(refname) %(objectname)"
        ordered newest -> oldest.
    last_version: The version string of the previously rolled revision.
  """
  matches = filter(bool, (REF_LINE_RE.fullmatch(line) for line in ref_lines))
  version_revisions = [match.groups() for match in matches]

  assert version_revisions, 'Did not find any recent release.'

  # There must be some progress between the last roll and the new candidate
  # revision (i.e. we don't go backwards). The revisions are ordered newest
  # to oldest. It is possible that the newest timestamp has no progress
  # compared to the last roll, e.g. if the newest release is a cherry-pick
  # on a release branch. Then we look further.
  for version, revision in version_revisions:
    if normalize_version(last_version) < normalize_version(version):
      return revision, f'found revision to roll: {revision}'
  return None, f'found no newer revision than: {last_version}'
