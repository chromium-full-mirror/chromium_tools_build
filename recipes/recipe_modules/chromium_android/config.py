# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.config import config_item_context, ConfigGroup
from recipe_engine.config import Dict, List, Single, Static


def BaseConfig(INTERNAL=False):
  return ConfigGroup(
      INTERNAL=Static(INTERNAL),
      cs_base_url=Single(
          str, required=False, empty_val='http://cs.chromium.org'),
      results_bucket=Single(
          str, required=False, empty_val='chromium-result-details'),
      get_app_manifest_vars=Single(bool, required=False, empty_val=True),
      run_tree_truth=Single(bool, required=False, empty_val=True),
      internal_dir_name=Single(str, required=False),
      extra_deploy_opts=List(inner_type=str),
      tests=List(inner_type=str),
      # Path to the test runner relative to the top level repo
      test_runner=Single(str),
      gclient_custom_deps=Dict(value_type=(str, type(None))),
      channel=Single(str, empty_val='chrome'),
      coverage=Single(bool, required=False, empty_val=False),
      incremental_coverage=Single(bool, required=False, empty_val=False),
      use_devil_adb=Single(bool, required=False, empty_val=False),
      # TODO(crbug.com/708171): Remove this once everything has switched to
      # devil provisioning.
      use_devil_provision=Single(bool, required=False, empty_val=False),
      remove_system_packages=List(inner_type=str),
      logcat_bucket=Single((str, type(None)),
                           required=False,
                           empty_val='chromium-android'),
  )


config_ctx = config_item_context(BaseConfig)

@config_ctx(is_root=True)
def base_config(c):
  c.internal_dir_name = 'clank'
  c.test_runner = 'build/android/test_runner.py'

@config_ctx()
def main_builder(_):
  pass

@config_ctx()
def main_builder_mb(_):
  pass

@config_ctx()
def main_builder_rel_mb(_):
  pass

@config_ctx()
def clang_builder(_):  # pragma: no cover
  pass

@config_ctx()
def clang_builder_mb(_):
  pass

@config_ctx(includes=['x64_builder_mb'])
def clang_builder_mb_x64(_):
  pass

@config_ctx()
def x86_base(_):
  pass

@config_ctx(includes=['x86_base'])
def x86_builder(_):
  pass

@config_ctx(includes=['x86_builder'])
def x86_builder_mb(_):
  pass


@config_ctx()
def riscv64_base(_):
  pass


@config_ctx(includes=['riscv64_base'])
def riscv64_builder(_):
  pass

@config_ctx()
def arm_v6_builder_rel(_):  # pragma: no cover
  pass

@config_ctx()
def x64_base(_):
  pass

@config_ctx(includes=['x64_base'])
def x64_builder(_):
  pass

@config_ctx(includes=['x64_builder'])
def x64_builder_mb(_):
  pass

@config_ctx()
def arm64_builder(_):
  pass

@config_ctx()
def arm64_builder_mb(_):
  pass

@config_ctx()
def arm64_builder_rel(_):  # pragma: no cover
  pass

@config_ctx()
def arm64_builder_rel_mb(_):
  pass

@config_ctx()
def chromium_perf(_):
  pass

@config_ctx()
def cast_builder(_):
  pass

@config_ctx()
def use_devil_adb(c):
  c.use_devil_adb = True

@config_ctx()
def use_devil_provision(c):
  c.use_devil_provision = True

@config_ctx(includes=['use_devil_provision'])
def remove_system_webview(c):
  c.remove_system_packages.extend(
      ['com.google.android.webview', 'com.android.webview'])

@config_ctx(includes=['use_devil_provision'])
def remove_system_webview_shell(c):
  c.remove_system_packages.append('org.chromium.webview_shell')

@config_ctx(includes=['use_devil_provision'])
def remove_system_chrome(c):
  c.remove_system_packages.append('com.android.chrome')

@config_ctx(includes=[
    'remove_system_chrome',
    'remove_system_webview',
    'remove_system_webview_shell'])
def remove_all_system_webviews(_):
  pass
