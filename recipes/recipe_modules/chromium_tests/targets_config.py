# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from . import steps

from RECIPE_MODULES.build.attr_utils import (
  attrib,
  attrs,
  cached_property,
  mapping,
  sequence,
)
from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc


@attrs()
class Targets:
  _tests = attrib(sequence[steps.AbstractTest])
  _additional_compile_targets = attrib(sequence[str])


@attrs()
class TargetsConfig:
  """Configuration about the targets to build and test for a builder.

  TargetsConfig provides access to information obtained from src-side files; for
  a given recipe version TargetsConfig information can change on a
  per-src-revision basis. There could potentially be multiple TargetsConfigs for
  the same build that contain different information (e.g. a trybot running
  against a change that modifies the src-side spec information).
  """

  builder_config = attrib(ctbc.BuilderConfig)
  _targets_by_builder_id = attrib(mapping[chromium_types.BuilderId, Targets])
  _skip_tests = attrib(sequence[str])

  @classmethod
  def create(cls, **kwargs):
    return cls(**kwargs)

  # NOTE: All of the tests_in methods are returning mutable objects
  # (and we expect them to be mutated, e.g., to update the swarming
  # command lines to use when we determine what they are).
  def _get_tests_for(self, keys):
    tests = []
    for k in keys:
      tests.extend(self._targets_by_builder_id[k]._tests)
    return tests

  @cached_property
  def all_tests(self):
    """Returns all tests in scope for the builder config."""
    return self._get_tests_for(
      self.builder_config.builder_ids_in_scope_for_testing
    )

  def tests_on(self, builder_id):
    """Returns all tests for the specified builder."""
    return self._get_tests_for([builder_id])

  def tests_triggered_by(self, builder_id):
    """Returns all tests for builders triggered by the specified builder."""
    return self._get_tests_for(
      self.builder_config.builder_db.builder_graph[builder_id]
    )

  @cached_property
  def compile_only_targets(self):
    """The compile-only targets to be built."""
    compile_targets = set()
    for targets in self._targets_by_builder_id.values():
      compile_targets.update(targets._additional_compile_targets)
    return sorted(compile_targets)

  @cached_property
  def compile_targets(self):
    """The compile targets to be built

    The compile targets to be built is the union of the compile targets needed
    for all tests and any additional compile targets requested by the builders
    being wrapped by the builder config.
    """
    compile_targets = set(self.compile_only_targets)
    for t in self.all_tests:
      # If/when ci_only tests shouldn't be compiled this can be replaced with
      # t.is_enabled
      if t.name not in self._skip_tests:
        compile_targets.update(t.compile_targets())
    return sorted(compile_targets)
