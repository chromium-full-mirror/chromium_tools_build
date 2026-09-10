# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api

from PB.recipe_modules.build.pgo import properties


class PgoTestApi(recipe_test_api.RecipeTestApi):
  def __call__(
    self,
    use_pgo=False,
    skip_profile_upload=False,
    profdata_platform_override=None,
  ):
    return self.m.properties(
      **{
        '$build/pgo': properties.InputProperties(
          use_pgo=use_pgo,
          skip_profile_upload=skip_profile_upload,
          profdata_platform_override=profdata_platform_override,
        ),
      }
    )
