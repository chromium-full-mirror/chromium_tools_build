# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api

from . import constants


class GnTestApi(recipe_test_api.RecipeTestApi):
  DEFAULT = constants.DEFAULT
  TEXT = constants.TEXT
  LOGS = constants.LOGS
