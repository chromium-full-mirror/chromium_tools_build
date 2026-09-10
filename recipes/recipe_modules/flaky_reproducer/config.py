# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.config import config_item_context, ConfigGroup
from recipe_engine.config import Single


def BaseConfig():
  return ConfigGroup(
    priority=Single(int),
    expiration=Single(int),
    strategy_timeout=Single(int),
    io_timeout=Single(int),
    verify_timeout=Single(int),
    verify_on_all_buckets=Single(bool),
    verify_only_cq_sheriff_builders=Single(bool),
  )


config_ctx = config_item_context(BaseConfig)


@config_ctx(is_root=True)
def BASE(c):
  c.priority = 200
  c.expiration = 1 * 60 * 60  # 1 hours
  c.strategy_timeout = 60 * 60  # 1 hour
  c.io_timeout = 20 * 60  # 20 mins
  c.verify_timeout = 20 * 60  # 20 mins
  c.verify_on_all_buckets = False
  c.verify_only_cq_sheriff_builders = True


@config_ctx(group='trigger_mode')
def auto(c):
  pass


@config_ctx(group='trigger_mode')
def manual(c):
  c.priority = 30
  c.expiration = 20 * 60  # 20 minutes
