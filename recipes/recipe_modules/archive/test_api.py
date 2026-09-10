# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api


class ArchiveApi(recipe_test_api.RecipeTestApi):
  def _read_source_side_archive_spec(
    self, filename, contents, step_prefix=None, step_suffix=None
  ):
    """Adds step data overrides for when a test reads source side archive
    specs.

    Args:
      * filename: Name of the source side archive spec file to read from.
      * contents: The contents of the source side archive spec file.
      * step_prefix: Any prefix to add to the step name. Useful if using step
        nesting.
      * step_suffix: Any suffix to append to the step name. Useful if the step
        runs twice.

    Returns:
      A recipe test object.
    """
    return self.override_step_data(
      '%sread archive spec (%s)%s'
      % (step_prefix or '', filename, step_suffix or ''),
      self.m.file.read_json(contents),
    )
