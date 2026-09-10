# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""API recipe module for ytdevinfra."""

from __future__ import annotations

from recipe_engine import recipe_api


class DevInfraApi(recipe_api.RecipeApi):
  def __init__(self, input_properties, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._version = input_properties.ytdevinfra_recipe_version

  def title(self, default_usecase="building"):
    self.m.step(
      'Print title from API module', ['echo', "Recipe for %s" % default_usecase]
    )
