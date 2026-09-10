# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
from recipe_engine import recipe_test_api

THIS_DIR = os.path.dirname(os.path.abspath(__file__))


class FlakyReproducerTestApi(recipe_test_api.RecipeTestApi):
  @staticmethod
  def get_test_path(filename):
    """Return test data filepath"""
    return os.path.join(THIS_DIR, 'resources', 'testdata', filename)

  @staticmethod
  def get_test_data(filename):
    """Return test data as str"""
    with open(FlakyReproducerTestApi.get_test_path(filename), 'rb') as fp:
      return fp.read()
