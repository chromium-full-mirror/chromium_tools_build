# Copyright 2020 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.recipe_engine import url


@dataclass
class DEPS(RecipeScriptApi):
  url: url.API


from .api import ChromiumDashApi as API
