# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Config for ytdevinfra recipe module."""

from __future__ import annotations

from recipe_engine.config import config_item_context, ConfigGroup
from recipe_engine.config import Single, Static, BadConf


def BaseConfig(TARGET='AndroidAPK'):
  return ConfigGroup(
    android_apk=Single(bool),
    tool=Single(str, required=True),
    TARGET=Static(str(TARGET)),
  )


config_ctx = config_item_context(BaseConfig)


@config_ctx(is_root=True)
def BASE(c):
  if c.TARGET == 'AndroidAPK':
    c.android_apk = True
  else:
    c.android_apk = False


@config_ctx(group='tool')
def ytdevinfra_tv(c):
  if c.TARGET == 'AndroidAPK':
    raise BadConf('Can\'t use TV tool for AndroidAPK.')
  c.tool = 'dab'


@config_ctx(group='tool')
def ytdevinfra_android(c):
  c.tool = 'adb'
