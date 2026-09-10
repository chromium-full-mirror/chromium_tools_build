# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api


class SymuploadTestApi(recipe_test_api.RecipeTestApi):
  def __call__(self, symupload_datas):
    return self.m.properties(
      **{
        '$build/symupload': symupload_datas,
      }
    )
