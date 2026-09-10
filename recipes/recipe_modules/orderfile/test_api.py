# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api

from PB.recipe_modules.build.orderfile import properties


class OrderfileTestApi(recipe_test_api.RecipeTestApi):
  def __call__(
    self,
    *,
    use_orderfile=False,
    upload_orderfile=False,
    gs_bucket="",
    gs_bucket_path="",
    last_uploaded_pgo_filename="",
  ):
    return self.m.properties(
      **{
        '$build/orderfile': properties.InputProperties(
          use_orderfile=use_orderfile,
          upload_orderfile=upload_orderfile,
          gs_bucket=gs_bucket,
          gs_bucket_path=gs_bucket_path,
          last_uploaded_pgo_filename=last_uploaded_pgo_filename,
        ),
      }
    )
