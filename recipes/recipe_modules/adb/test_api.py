# Copyright 2014 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api


class AdbTestApi(recipe_test_api.RecipeTestApi):
  def device_list(self):
    return self.m.json.output(["014E1F310401C009"])
