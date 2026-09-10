# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api

from PB.recipe_modules.build.pinlist import properties


class PinlistTestApi(recipe_test_api.RecipeTestApi):
  def __call__(self, *, upload_pinlist=False):
    return self.m.properties(
      **{
        '$build/pinlist': properties.InputProperties(
          upload_pinlist=upload_pinlist
        ),
      }
    )
