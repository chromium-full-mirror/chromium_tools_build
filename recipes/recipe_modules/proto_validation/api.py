# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api


class ProtoUtilsApi(recipe_api.RecipeApi):
  def __init__(self, *args, **kwargs):
    raise Exception(  # pragma: no cover
      'API has no methods, instead import RECIPE_MODULE.build.proto_validation'
    )
