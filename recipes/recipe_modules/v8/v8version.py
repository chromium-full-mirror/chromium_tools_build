# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re

from datetime import datetime

REF_LINE_RE = re.compile(
  r'refs\/tags\/(\d+(?:\.\d+){2,3})((?:-pgo)?)\ ([0-9a-f]{40})\ (.*)'
)

V8_PATCHED_VERSION_RE = re.compile(r'\d+(?:\.\d+){3}')

VersionTuple = tuple[int, int, int, int]

# Offset by which we still try to roll a patched version when a newer unpatched
# version exists.
PATCH_VERSION_OFFSET_HOURS = 3


def normalize_version(version) -> VersionTuple:
  """Accept multiple input types to represent a version, and return a
  normalized version tuple.

  Supported input types:
    * Tuple of integers of various lengths, e.g. (12, ) (12, 5), (12, 5, 1, 9)
    * Dot-separated string of various length, e.g. '12', '12.5', '12.5.1.9'
  """
  if isinstance(version, str):
    version = tuple(version.split('.'))

  assert isinstance(version, tuple), f'Expected a tuple, found {type(version)}.'

  assert 0 < len(version) <= 4

  version = tuple(int(c) for c in version)

  return (version + (0,) * 3)[:4]


def is_patched(version):
  return bool(V8_PATCHED_VERSION_RE.fullmatch(version))


def prioritize_patched(version_revision):
  """Sort key for sorting version tuples by timestamp prioritizing patched
  versions.
  """
  version, _, timestamp = version_revision
  penalty = 0 if is_patched(version) else PATCH_VERSION_OFFSET_HOURS * 60 * 60
  return timestamp - penalty, version


def largest_major_version(versions):
  """Returns the largest major version from a list of versions.

  E.g. for 1.2.3, 1.3.1.1, 1.2.133.4 it would return (1.3) as a tuple.
  """
  assert versions
  return max(normalize_version(version)[:2] for version in versions)


def drop_version_padding(version):
  """Ensures padded versions are ignored for selecting roll candidates by
  removing the padded build level from the version.
  """
  if version[2] > 999:
    version = list(version)
    version[2] = 0
    return tuple(version)
  return version


def sorted_improvements(version_revisions, last_version):
  """Returns version tuples of strictly newer versions sorted by
  commit time prioritizing patches.

  All non-patch versions have a commit-time penalty of
  PATCH_VERSION_OFFSET_HOURS.

  Only the version tuples of the largest major version are
  returned.
  """
  last_version_normalized = normalize_version(last_version)

  # Ensure that we never by mistake roll revisions that are treated as padded
  # versions below. Typical release cycles stay far below 500. This safeguard
  # theoretically allows to roll up to version 899 and then flag new rolls as
  # failure. If this ever happens, this number could be flexed to 999 as a
  # mitigation.
  assert last_version_normalized[2] < 899, (
    'Rolling padded versions is not supported.'
  )

  timestamp = lambda commit_time: datetime.strptime(
    commit_time, '%a %b %d %H:%M:%S %Y %z'
  ).timestamp()
  improvements = [
    (version, revision, timestamp(commit_time))
    for version, pgo, revision, commit_time in version_revisions
    if not pgo
    and last_version_normalized
    < drop_version_padding(normalize_version(version))
  ]
  if not improvements:
    return
  major_version = largest_major_version(v for v, _, _ in improvements)
  for version, revision, _ in sorted(
    improvements, key=prioritize_patched, reverse=True
  ):
    if major_version < drop_version_padding(normalize_version(version)):
      yield version, revision


def choose_revision_to_roll(ref_lines, last_version):
  """Choose the next V8 revision to roll based on recent tags.

  This algorithm ensures the new version is strictly newer than the
  last version and has pgo data available.

  If a patched version is available, the latest patched version has
  priority for PATCH_VERSION_OFFSET_HOURS hours.

  Args:
    ref_lines: List ref strings in the format:
        "%(refname) %(objectname) %(committerdate)".
    last_version: The version string of the previously rolled revision.
  """
  # Matches grouped as (version, pgo_suffix, revision, commit_time).
  matches = filter(bool, (REF_LINE_RE.fullmatch(line) for line in ref_lines))
  version_revisions = [match.groups() for match in matches]

  assert version_revisions, 'Did not find any recent release.'

  # Versions with pgo tag.
  pgo_versions = set(v[0] for v in version_revisions if v[1])

  for version, revision in sorted_improvements(version_revisions, last_version):
    if version in pgo_versions:
      return revision, f'found revision to roll: {revision}'
    if is_patched(version):
      return None, f'waiting for pgo data for: {revision}'
  return None, f'found no newer revision than: {last_version}'
