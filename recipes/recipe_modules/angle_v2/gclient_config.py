# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from RECIPE_MODULES.depot_tools.gclient import CONFIG_CTX
from RECIPE_MODULES.depot_tools.gclient.config import ChromiumGitURL


@CONFIG_CTX()
def angle_v2(c):
  soln = c.solutions.add()
  soln.name = 'angle'
  soln.url = ChromiumGitURL(c, 'angle', 'angle')

  # Standalone developer angle builds want the angle checkout in the same
  # directory the .gclient file is in.  Bots want it in a directory called
  # 'angle'.  To make both cases work, the angle DEPS file pulls deps and runs
  # hooks relative to the variable "root" which is set to . by default and
  # then to 'angle' in the recipes here:
  soln.custom_vars = {'angle_root': 'angle'}
  soln.custom_vars['checkout_angle_internal'] = True

  c.got_revision_mapping['angle'] = 'got_revision'
  c.got_revision_reverse_mapping['got_angle_revision'] = 'angle'


@CONFIG_CTX(includes=['angle_v2'])
def angle_v2_android(c):
  c.target_os.add('android')


@CONFIG_CTX(includes=['angle_v2'])
def angle_v2_nointernal(c):
  c.solutions[0].custom_vars['checkout_angle_internal'] = False
