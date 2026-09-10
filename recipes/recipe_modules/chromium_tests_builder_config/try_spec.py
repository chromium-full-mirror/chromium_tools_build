# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections.abc

from RECIPE_MODULES.build.attr_utils import (
  attrib,
  attrs,
  enum,
  mapping,
  sequence,
)
from RECIPE_MODULES.build.chromium_types import BuilderId

COMPILE_AND_TEST = 'compile/test'
COMPILE = 'compile'

ALWAYS = 'always'
NEVER = 'never'


@attrs()
class TryMirror:
  """An immutable specification for a trybot-mirroring relationship."""

  # The mirrored builder
  builder_id = attrib(BuilderId)
  # An optional builder that specifies tests that will be executed by the
  # associated trybot
  tester_id = attrib(BuilderId, default=None)

  def __attrs_post_init__(self):
    assert self.tester_id != self.builder_id, (
      "'tester_id' should not be equal to 'builder_id',"
      " pass None for 'tester_id'"
    )

  @classmethod
  def create(cls, builder_group, buildername, tester=None, tester_group=None):
    """Create a TryMirror.

    Args:
      builder_group - The name of the mirrored builder's group.
      buildername - The name of the mirrored builder.
      tester - The name of the mirrored tester. If not provided, the
        returned TryMirror's tester_id field will be None.
      tester_group - The name of the mirrored tester's group. If not
        provided and tester is provided, then builder_group will be
        used. Has no effect if tester is not provided.

    Returns:
      A TryMirror object. builder_id will be set to a BuilderId that
      identifies the mirrored builder. If tester is provided and the
      mirrored tester is not the same as the mirrored builder, tester_id
      will be set to a BuilderId that identifies the mirrored tester.
      Otherwise, tester_id will be set to None.
    """
    builder_id = BuilderId.create_for_group(builder_group, buildername)
    tester_id = None
    if tester is not None:
      tester_id = BuilderId.create_for_group(
        tester_group or builder_group, tester
      )
      if tester_id == builder_id:
        tester_id = None
    return cls(builder_id, tester_id)

  @classmethod
  def normalize(cls, mirror):
    """Converts various representations of a mirror to TryMirror.

    The incoming representation can have one of the following forms:
    * TryMirror - The input is returned.
    * BuilderId - A TryMirror with `builder_id` set to the input is
      returned.
    """
    if isinstance(mirror, BuilderId):
      return cls(builder_id=mirror)
    assert isinstance(mirror, TryMirror)
    return mirror


@attrs()
class TrySpec:
  """Immutable specification for the operation of a try builder."""

  # The specifications of the builders being mirrored by the try builder
  mirrors = attrib(sequence[TryMirror])
  # Whether or not all testers triggered by builders in mirrors should be
  # considered in scope for testing
  include_all_triggered_testers = attrib(bool, default=False)
  # Whether the try builder is compile only or not
  is_compile_only = attrib(bool, default=False)
  # Additional names to add when analyzing the change to determine affected
  # targets
  analyze_names = attrib(sequence[str], default=())
  # Whether or not failed shards of tests should be retried
  retry_failed_shards = attrib(bool, default=True)
  # Whether or not failed test suites should be retried without patch
  retry_without_patch = attrib(bool, default=True)
  # Conditions where skipping a subset of tests is allowed
  regression_test_selection = attrib(enum([ALWAYS, NEVER]), default=NEVER)

  @classmethod
  def create(cls, mirrors, **kwargs):
    """Create a TrySpec.

    Args:
      mirrors - A sequence of values that can be normalized to a TryMirror.
      kwargs - Values to apply to additional fields of the TrySpec.

    Returns:
      A TrySpec where:
      * The mirrors field is initialized with TryMirror instances normalized
        from mirrors.
      * The remaining fields are initialized with the values passed in kwargs.
    """
    assert mirrors
    mirrors = [TryMirror.normalize(m) for m in mirrors]
    return cls(mirrors=mirrors, **kwargs)

  @classmethod
  def create_for_single_mirror(
    cls, builder_group, buildername, tester=None, tester_group=None, **kwargs
  ):
    """Create a TrySpec with a single mirror.

    Args:
      builder_group - The name of the mirrored builder's group.
      buildername - The name of the mirrored builder.
      tester - The name of the mirrored tester. If not provided, the
        TrySpec's mirror's tester_id field will be None.
      tester_group - The name of the mirrored tester's group. If not
        provided and tester is provided, then builder_group will be
        used. Has no effect if tester is not provided.
      kwargs - Values to apply to additional fields of the TrySpec.

    Returns:
      A TrySpec where:
      * The mirrors field has a single TryMirror instance create by
        calling TryMirror.create with builder_group, buildername, tester
        and tester_group.
      * The remaining fields are initialized with the values passed in kwargs.
    """
    return cls.create(
      mirrors=[
        TryMirror.create(builder_group, buildername, tester, tester_group)
      ],
      **kwargs,
    )


@attrs()
class TryDatabase(collections.abc.Mapping):
  """A database that provides information for multiple try groups.

  TryDatabase provides access to the information contained in TryGroupSpec
  instances for multiple groups. Individual try builders can be looked up
  using mapping access with BuilderId as keys and TrySpec as values.
  """

  _db = attrib(mapping[BuilderId, TrySpec])

  @classmethod
  def create(cls, trybots_dict):
    """Create a TryDatabase from a dict.

    Args:
      trybots_dict - The mapping containing the information to create
        the database from. The keys of the mapping are the names of the
        try groups. The values of the mapping are themselves mappings
        with the keys being builder names and the values TryMirror
        instances for the associated builder.

    Returns:
    A new BuilderDatabase instance providing access to the information in
    trybots_dict.
    """
    db = {}

    for group, trybots_for_group in trybots_dict.items():
      for builder_name, try_spec in trybots_for_group.items():
        builder_id = BuilderId.create_for_group(group, builder_name)
        db[builder_id] = try_spec

    return cls(db)

  def __getitem__(self, key):
    return self._db[key]

  def __iter__(self):
    return iter(self._db)

  def __len__(self):
    return len(self._db)
