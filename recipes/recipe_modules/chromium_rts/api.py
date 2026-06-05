# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_api

class ChromiumRtsApi(recipe_api.RecipeApi):
  """A module for interacting with rts."""

  def generate_filter_files(self, src_dir, build_dir, tests):
    """Generates .filter files for the given tests."""
    # TODO: Part of a chained CL currently in review process.
    raise NotImplementedError('generate_filter_files is not implemented')
