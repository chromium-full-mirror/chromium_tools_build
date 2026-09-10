# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api

from RECIPE_MODULES.build.chromium_tests_builder_config import (
  builder_db,
  try_spec,
)


class DawnTestsApi(recipe_test_api.RecipeTestApi):
  @recipe_test_api.mod_test_data
  def builders(self, builders):
    """Override test builders for a test.

    Args:
      builders - A BuilderDatabase to replace dawn.builders.
    """
    assert isinstance(builders, builder_db.BuilderDatabase)
    return builders

  @recipe_test_api.mod_test_data
  def trybots(self, trybots):
    """Override test builders for a test.

    Args:
      trybots - A TryDatabase to replace dawn.trybots.
    """
    assert isinstance(trybots, try_spec.TryDatabase)
    return trybots

  def ci_build(self, **kwargs):
    """Create test data for a CI build.

    Effectively a wrapper around chromium.ci_build with some extra checks and
    default values.
    """
    self._validate_kwargs_and_set_defaults(kwargs)
    return self.m.chromium.ci_build(**kwargs)

  def try_build(self, **kwargs):
    """Create test data for a try build.

    Effectively a wrapper around chromium.try_build with some extra checks and
    default values.
    """
    self._validate_kwargs_and_set_defaults(kwargs)
    return self.m.chromium.try_build(**kwargs)

  def _validate_kwargs_and_set_defaults(self, kwargs):
    assert 'use_try_db' not in kwargs
    kwargs.setdefault('builder_group', 'dawn')
    kwargs.setdefault('project', 'dawn')
    kwargs.setdefault('git_repo', 'https://dawn.googlesource.com/dawn/')
