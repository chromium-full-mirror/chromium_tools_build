# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                try_spec)


class ANGLETestsApi(recipe_test_api.RecipeTestApi):

  @recipe_test_api.mod_test_data
  @staticmethod
  def builders(builders):
    """Override test builders for a test.

    Args:
      builders - A BuilderDatabase to replace angle.builders.
    """
    assert isinstance(builders, builder_db.BuilderDatabase)
    return builders

  @recipe_test_api.mod_test_data
  @staticmethod
  def trybots(trybots):
    """Override test builders for a test.

    Args:
      trybots - A TryDatabase to replace angle.trybots.
    """
    assert isinstance(trybots, try_spec.TryDatabase)
    return trybots

  def ci_build(self,
               toolchain='clang',
               platform='linux',
               test_mode='compile_and_test',
               **kwargs):
    """Create test data for a CI build.

    Adding this to a test will set properties and inputs in a manner
    that is compatible with the chromium and
    chromium_tests_builder_config modules for builds that would be
    triggered by a scheduler poller with an associated gitiles commit
    (or triggered by another builder that was triggered by a scheduler
    poller).

    All keyword arguments supported by chromium.generic_build are
    supported, as well as the following additional keyword arguments:
    * builder_db - Overrides the default builder database.
    * try_db - Override the default try database. Can only be specified
      if builder_db is also specified. If builder_db is specified and
      try_db is not specified, an empty try database will be used as the
      default try database.
    """
    assert 'use_try_db' not in kwargs
    kwargs.setdefault('builder', 'linux-builder')
    kwargs.setdefault('builder_group', 'angle')
    kwargs.setdefault('git_repo',
                      'https://chromium.googlesource.com/angle/angle.git')
    kwargs.setdefault('project', 'angle')
    return self.m.chromium.ci_build(**kwargs) + self.m.properties(
        toolchain=toolchain, platform=platform, test_mode=test_mode)

  def try_build(self,
                toolchain='clang',
                platform='linux',
                test_mode='compile_and_test',
                **kwargs):  # pragma: no cover
    """Create test data for a try build.

    Adding this to a test will set properties and inputs in a manner
    that is compatible with the chromium and
    chromium_tests_builder_config modules for try builds with an
    associated gerrit change.

    All keyword arguments supported by chromium.generic_build are
    supported, as well as the following additional keyword arguments:
    * builder_db - Overrides the default builder database.
    * try_db - Override the default try database. Can only be specified
      if builder_db is also specified. If builder_db is specified and
      try_db is not specified, an empty try database will be used as the
      default try database.
    """
    assert 'use_try_db' not in kwargs
    kwargs.setdefault('builder', 'linux-builder')
    kwargs.setdefault('builder_group', 'angle')
    kwargs.setdefault('git_repo',
                      'https://chromium.googlesource.com/angle/angle.git')
    kwargs.setdefault('project', 'angle')
    return self.m.chromium.try_build(**kwargs) + self.m.properties(
        toolchain=toolchain, platform=platform, test_mode=test_mode)

  def override_commit_pos_data(self):
    return self.override_step_data(
        'get commit position', stdout=self.m.raw_io.output_text('1'))
