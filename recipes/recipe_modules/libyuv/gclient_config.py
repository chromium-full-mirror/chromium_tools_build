# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from RECIPE_MODULES.depot_tools.gclient import CONFIG_CTX
from RECIPE_MODULES.depot_tools.gclient.config import ChromiumGitURL


@CONFIG_CTX()
def libyuv(c):
  s = c.solutions.add()
  s.name = 'src'
  s.url = ChromiumGitURL(c, 'libyuv', 'libyuv')
  s.deps_file = 'DEPS'
  c.got_revision_mapping['src'] = 'got_revision'


@CONFIG_CTX(includes=['libyuv', 'android'])
def libyuv_android(_):
  pass


@CONFIG_CTX(includes=['libyuv'])
def libyuv_ios(c):
  c.target_os.add('mac')
  c.target_os.add('ios')
