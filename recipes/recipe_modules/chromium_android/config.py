# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.config import config_item_context, ConfigGroup
from recipe_engine.config import List, Single, Static


def BaseConfig(INTERNAL=False):
  return ConfigGroup(
    INTERNAL=Static(INTERNAL),
    cs_base_url=Single(str, required=False, empty_val='http://cs.chromium.org'),
    results_bucket=Single(
      str, required=False, empty_val='chromium-result-details'
    ),
    # Path to the test runner relative to the top level repo
    test_runner=Single(str),
    use_devil_adb=Single(bool, required=False, empty_val=False),
    # TODO(crbug.com/708171): Remove this once everything has switched to
    # devil provisioning.
    use_devil_provision=Single(bool, required=False, empty_val=False),
    remove_system_packages=List(inner_type=str),
    logcat_bucket=Single(
      (str, type(None)), required=False, empty_val='chromium-android'
    ),
  )


config_ctx = config_item_context(BaseConfig)


@config_ctx(is_root=True)
def base_config(c):
  c.test_runner = 'build/android/test_runner.py'


@config_ctx()
def use_devil_adb(c):
  c.use_devil_adb = True


@config_ctx()
def use_devil_provision(c):
  c.use_devil_provision = True


@config_ctx(includes=['use_devil_provision'])
def remove_system_webview(c):
  c.remove_system_packages.extend(
    ['com.google.android.webview', 'com.android.webview']
  )


@config_ctx(includes=['use_devil_provision'])
def remove_system_webview_shell(c):
  c.remove_system_packages.append('org.chromium.webview_shell')


@config_ctx(includes=['use_devil_provision'])
def remove_system_chrome(c):
  c.remove_system_packages.append('com.android.chrome')


@config_ctx(
  includes=[
    'remove_system_chrome',
    'remove_system_webview',
    'remove_system_webview_shell',
  ]
)
def remove_all_system_webviews(_):
  pass
