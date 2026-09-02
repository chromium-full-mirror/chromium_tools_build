# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.recipe_engine import (
    cipd,
    platform,
)


@dataclass
class DEPS(RecipeScriptApi):
  cipd: cipd.API
  platform: platform.API

from .api import GaeSdkApi as API
