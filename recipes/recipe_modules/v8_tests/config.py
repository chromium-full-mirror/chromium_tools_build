# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.config import config_item_context, ConfigGroup
from recipe_engine.config import List, Single, Static


def BaseConfig(**_kwargs):
  return ConfigGroup(
    # Test configuration that is equal for all tests of a builder. It
    # might be refined later in the test runner for distinct tests.
    testing=ConfigGroup(
      test_args=List(str),
      may_shard=Single(bool, empty_val=True, required=False),
    ),
  )


config_ctx = config_item_context(BaseConfig)


@config_ctx()
def v8(_):
  pass
