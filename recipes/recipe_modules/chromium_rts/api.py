# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_api

_DISABLE_RTS_FOOTER = 'Disable-Rts'


class ChromiumRtsApi(recipe_api.RecipeApi):
  """A module for interacting with rts."""

  def is_rts_footer_disabled(self):
    disabled = False
    if self.m.tryserver.is_tryserver:
      footer_vals = self.m.tryserver.get_footer(_DISABLE_RTS_FOOTER)
      if footer_vals:
        disabled = footer_vals[-1].lower() == 'true'
    return disabled
