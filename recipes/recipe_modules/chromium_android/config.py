# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.config import config_item_context, ConfigGroup
from recipe_engine.config import Dict, List, Single, Static
from recipe_engine.config_types import Path

def BaseConfig(CHECKOUT_PATH, INTERNAL=False, REPO_NAME=None, REPO_URL=None,
               BUILD_CONFIG='Debug', REVISION='', asan_symbolize=False,
               **_kwargs):  # pylint: disable=redefined-outer-name
  return ConfigGroup(
      CHECKOUT_PATH=Static(CHECKOUT_PATH),
      INTERNAL=Static(INTERNAL),
      REPO_NAME=Static(REPO_NAME),
      REPO_URL=Static(REPO_URL),
      BUILD_CONFIG=Static(BUILD_CONFIG),
      cs_base_url=Single(
          str, required=False, empty_val='http://cs.chromium.org'),
      results_bucket=Single(
          str, required=False, empty_val='chromium-result-details'),
      revision=Single(str, empty_val=REVISION),
      revisions=Dict(value_type=(str, type(None))),
      asan_symbolize=Single(bool, required=False, empty_val=asan_symbolize),
      get_app_manifest_vars=Single(bool, required=False, empty_val=True),
      run_tree_truth=Single(bool, required=False, empty_val=True),
      deps_file=Single(str, required=False, empty_val='.DEPS.git'),
      internal_dir_name=Single(str, required=False),
      # deps_dir: where to checkout the gclient deps file
      deps_dir=Single(str, required=False, empty_val=REPO_NAME),
      managed=Single(bool, required=False, empty_val=True),
      extra_deploy_opts=List(inner_type=str),
      tests=List(inner_type=str),
      cr_build_android=Static(
          CHECKOUT_PATH.join('build', 'android') if CHECKOUT_PATH else None),
      test_runner=Single(Path),
      resource_sizes=Single(Path),
      gclient_custom_deps=Dict(value_type=(str, type(None))),
      channel=Single(str, empty_val='chrome'),
      gclient_custom_vars=Dict(value_type=(str, type(None))),
      coverage=Single(bool, required=False, empty_val=False),
      chrome_specific_wipe=Single(bool, required=False, empty_val=False),
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
  if c.CHECKOUT_PATH:
    c.test_runner = c.CHECKOUT_PATH.join('build', 'android', 'test_runner.py')
    c.resource_sizes = c.CHECKOUT_PATH.join('build', 'android',
                                            'resource_sizes.py')

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


@config_ctx(includes=['riscv64_builder'])
def riscv64_builder_mb(_):
  pass


@config_ctx()
def mipsel_base(_):
  pass

@config_ctx(includes=['mipsel_base'])
def mipsel_builder(_):  # pragma: no cover
  pass

@config_ctx(includes=['mipsel_base'])
def mipsel_builder_mb(_):
  pass

@config_ctx()
def arm_l_builder(_):  # pragma: no cover
  pass

@config_ctx()
def arm_l_builder_lto(_):  # pragma: no cover
  pass

@config_ctx()
def arm_l_builder_rel(_):  # pragma: no cover
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
def try_base(_):
  pass  # pragma: no cover

@config_ctx(includes=['try_base'])
def try_builder(_):
  pass  # pragma: no cover

@config_ctx(includes=['x86_builder', 'try_builder'])
def x86_try_builder(_):
  pass  # pragma: no cover

@config_ctx()
def tests_base(_):  # pragma: no cover
  pass

@config_ctx(includes=['arm64_builder_rel'])
def tests_arm64(_):  # pragma: no cover
  pass

@config_ctx(includes=['tests_base'])
def instrumentation_tests(c):  # pragma: no cover
  c.tests.append('smoke_instrumentation_tests')
  c.tests.append('small_instrumentation_tests')
  c.tests.append('medium_instrumentation_tests')
  c.tests.append('large_instrumentation_tests')

@config_ctx(includes=['instrumentation_tests'])
def main_tests(_):
  pass  # pragma: no cover

@config_ctx(includes=['asan_symbolize', 'tests_base'])
def clang_tests(c):  # pragma: no cover
  c.tests.append('smoke_instrumentation_tests')

@config_ctx(includes=['tests_base'])
def enormous_tests(c):  # pragma: no cover
  c.extra_deploy_opts = ['--await-internet']
  c.tests.append('enormous_instrumentation_tests')

@config_ctx(includes=['try_base', 'instrumentation_tests'])
def try_instrumentation_tests(_):
  pass  # pragma: no cover

@config_ctx(includes=['x86_base', 'try_base', 'instrumentation_tests'])
def x86_try_instrumentation_tests(c):
  c.extra_deploy_opts.append('--non-rooted')  # pragma: no cover

@config_ctx(includes=['main_builder'])
def coverage_builder_tests(_):  # pragma: no cover
  pass

@config_ctx(includes=['main_builder'])
def non_device_wipe_provisioning(c):
  c.chrome_specific_wipe = True

@config_ctx(includes=['main_builder'])
def incremental_coverage_builder_tests(c):
  c.incremental_coverage = True

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
def remove_system_vrcore(c):
  c.remove_system_packages.append('com.google.vr.vrcore')

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

@config_ctx()
def asan_symbolize(c):  # pragma: no cover
  c.asan_symbolize = True
