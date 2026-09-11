# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_toolchain import (
  properties as properties_pb,
)
from recipe_engine import recipe_test_api


class ChromiumToolchainTestApi(recipe_test_api.RecipeTestApi):
  InputProperties = properties_pb.InputProperties
  ToolchainType = properties_pb.InputProperties.ToolchainType

  def properties(self, toolchain=None, **kwargs):
    if toolchain is not None:
      kwargs['toolchain'] = toolchain
    return self.m.properties(**{'$build/chromium_toolchain': kwargs})
