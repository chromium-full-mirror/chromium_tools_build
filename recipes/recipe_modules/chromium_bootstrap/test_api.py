# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api

from PB.infra.chromium import chromium_bootstrap


class ChromiumBootstrapApi(recipe_test_api.RecipeTestApi):
  def properties(self, commits=None, skip_analysis_reasons=None, exe=None):
    return self.m.properties(
      **{
        '$build/chromium_bootstrap': chromium_bootstrap.ChromiumBootstrapModuleProperties(
          commits=commits or [],
          skip_analysis_reasons=skip_analysis_reasons or [],
          exe=exe or None,
        )
      }
    )
