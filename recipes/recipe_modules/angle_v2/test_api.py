# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                try_spec)


class ANGLEV2TestsApi(recipe_test_api.RecipeTestApi):

  @recipe_test_api.mod_test_data
  #@staticmethod
  def builders(self, builders):
    """Override test builders for a test.

    Args:
      builders - A BuilderDatabase to replace angle.builders.
    """
    assert isinstance(builders, builder_db.BuilderDatabase)
    return builders

  @recipe_test_api.mod_test_data
  #@staticmethod
  def trybots(self, trybots):
    """Override test builders for a test.

    Args:
      trybots - A TryDatabase to replace angle.trybots.
    """
    assert isinstance(trybots, try_spec.TryDatabase)
    return trybots

  def ci_build(self, **kwargs):
    """Create test data for a CI build using the v2 codepath.

    Effectively a wrapper around chromium.ci_build with some extra checks and
    default values.
    """
    self._validate_kwargs_and_set_defaults(kwargs)
    return self.m.chromium.ci_build(**kwargs)

  def try_build(self, **kwargs):
    """Create test data for a try build using the v2 codepath.

    Effectively a wrapper around chromium.try_build with some extra checks and
    default values.
    """
    self._validate_kwargs_and_set_defaults(kwargs)
    return self.m.chromium.try_build(**kwargs)

  def _validate_kwargs_and_set_defaults(self, kwargs):
    assert 'use_try_db' not in kwargs
    kwargs.setdefault('builder_group', 'angle')
    kwargs.setdefault('project', 'angle')
    kwargs.setdefault('git_repo',
                      'https://chromium.googlesource.com/angle/angle/')
