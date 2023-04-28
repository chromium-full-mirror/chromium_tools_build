# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from . import steps

from RECIPE_MODULES.build.attr_utils import (attrib, attrs, cached_property,
                                             mapping, sequence)
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc


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
  # The values should be a mapping from builder name to the raw targets spec for
  # the builder (mapping[str, mapping[str, ...]]), but if we enforce that here,
  # then a bad spec for one builder would cause all builders that read that file
  # to fail, so we don't apply any constraints to the value here to limit the
  # blast radius of bad changes
  _targets_specs = attrib(mapping[str, ...])
  _tests = attrib(mapping[chromium.BuilderId, sequence[steps.Test]])

  @classmethod
  def create(cls, **kwargs):
    return cls(**kwargs)

  # NOTE: All of the tests_in methods are returning mutable objects
  # (and we expect them to be mutated, e.g., to update the swarming
  # command lines to use when we determine what they are).
  def _get_tests_for(self, keys):
    tests = []
    for k in keys:
      tests.extend(self._tests[k])
    return tests

  @cached_property
  def all_tests(self):
    """Returns all tests in scope for the builder config."""
    return self._get_tests_for(
        self.builder_config.builder_ids_in_scope_for_testing)

  def tests_on(self, builder_id):
    """Returns all tests for the specified builder."""
    return self._get_tests_for([builder_id])

  def tests_triggered_by(self, builder_id):
    """Returns all tests for builders triggered by the specified builder."""
    return self._get_tests_for(
        self.builder_config.builder_db.builder_graph[builder_id])

  @cached_property
  def compile_targets(self):
    """The compile targets to be built

    The compile targets to be built is the union of the compile targets needed
    for all tests and any additional compile targets requested by the builders
    being wrapped by the builder config.
    """
    compile_targets = set()

    for builder_id in self.builder_config.builder_ids:
      targets_spec = self._targets_specs[builder_id.group].get(
          builder_id.builder, {})
      compile_targets.update(targets_spec.get('additional_compile_targets', []))

    for t in self.all_tests:
      compile_targets.update(t.compile_targets())

    return sorted(compile_targets)
