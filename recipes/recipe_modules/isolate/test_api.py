# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api


class IsolateTestApi(recipe_test_api.RecipeTestApi):
  def output_json(self, targets, missing=None):
    """Mocked output of 'find_isolated_tests' and 'isolate_tests' steps.

    Deterministically synthesizes json.output test data for the given targets.

    If |missing| is given it's a subset of |targets| that wasn't isolated in
    'isolate_tests' due to some error.
    """
    missing = missing or ()
    return self.m.json.output(
      {
        target: None
        if target in missing
        else '[dummy hash for %s/dummy size]' % target
        for target in targets
      }
    )
