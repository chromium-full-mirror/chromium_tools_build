# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api


class ChromiumCheckoutTestApi(recipe_test_api.RecipeTestApi):
  @property
  def default_checkout_dir(self):
    return self.m.path.cache_dir / 'builder'
