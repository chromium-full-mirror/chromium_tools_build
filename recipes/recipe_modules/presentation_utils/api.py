# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api


class PresentationUtilsApi(recipe_api.RecipeApi):
  @staticmethod
  def format_step_text(data):
    """Returns a string suitable as a step result's presentation step text.

    Args:
      data: iterable of sections, where each section is a tuple/list with two
          elements where first one is the header, and the second one is an
          iterable of content lines; if there are no contents, the whole section
          is not displayed
    """
    assert all(len(s) == 2 for s in data), (
      'All items in data must be a two-element list.'
    )
    step_text = []
    for section in data:
      # Only displaying the section (even the header) when it's non-empty
      # simplifies caller code.
      if section[1]:
        step_text.append('<br/>%s<br/>' % section[0])
        step_text.extend(('%s<br/>' % line for line in section[1]))
    return ''.join(step_text)
