# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


from __future__ import annotations

from recipe_engine import recipe_api


class ToolsBuildApi(recipe_api.RecipeApi):

  @property
  def bot_utils_args(self):
    """Returns (list): A list of arguments to supply to configure bot_utils
        parameters. See `bot_utils.py`'s AddArgs method.

    TODO(dnj): This function and its invocations should be deprecated in favor
    of using environment variables via "add_bot_utils_kwargs". The script
    invocation path for some of these is just too intertwined to confidently
    apply this via explicit args everywhere.
    """
    return [
        '--bot-utils-gsutil-py-path',
        self.m.depot_tools.gsutil_py_path,
    ]
