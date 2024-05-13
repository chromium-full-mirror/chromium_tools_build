# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import contextlib

from recipe_engine import recipe_api
from . import builders as libyuv_builders


# Builders that don't use remote compile service.
_LOCAL_COMPILE_BUILDERS = [
    'Linux GCC',
    'Win32 Debug',
    'Win32 Release',
    'Win64 Debug',
    'Win64 Release',
    'linux_gcc',
    'win',
    'win_rel',
    'win_x64_rel',
]


class LibyuvApi(recipe_api.RecipeApi):
  BUILDERS = libyuv_builders.BUILDERS
  RECIPE_CONFIGS = libyuv_builders.RECIPE_CONFIGS

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self.group_config = None
    self.bot_config = None
    self.bot_type = None
    self.buildername = None
    self.recipe_config = None
    self.revision = ''

  def apply_bot_config(self, builders, recipe_configs):
    builder_group = self.m.builder_group.for_current
    self.buildername = self.m.buildbucket.builder_name
    group_dict = builders.get(builder_group, {})
    self.group_config = group_dict.get('settings', {})

    self.bot_config = group_dict.get('builders', {}).get(self.buildername)
    assert self.bot_config, ('Unrecognized builder name "%r" for group "%r".' %
                             (self.buildername, builder_group))

    self.bot_type = self.bot_config['bot_type']
    recipe_config_name = self.bot_config['recipe_config']
    self.recipe_config = recipe_configs.get(recipe_config_name)
    assert self.recipe_config, (
        'Cannot find recipe_config "%s" for builder "%r".' %
        (recipe_config_name, self.buildername))

    chromium_kwargs = self.bot_config.get('chromium_config_kwargs', {})
    if self.recipe_config.get('chromium_android_config'):
      self.m.chromium_android.set_config(
          self.recipe_config['chromium_android_config'], **chromium_kwargs)

    self.m.chromium.set_config(self.recipe_config['chromium_config'],
                               **chromium_kwargs)
    self.m.gclient.set_config(self.recipe_config['gclient_config'])

    # Support applying configs both at the bot and the recipe config level.
    for c in self.bot_config.get('chromium_apply_config', []):
      self.m.chromium.apply_config(c)

    self.m.chromium.apply_config('gn')

    if self.m.tryserver.is_tryserver:
      if self.m.platform.is_win:
        # Windows builds are currently failing due to crbug.com/659439 when
        # fastbuild is enabled since it implies symbol_level=2. So we can only
        # enable dcheck for those.
        self.m.chromium.apply_config('dcheck')
      elif 'ubsan' in self.buildername.lower():
        pass  # UBSAN with dchecks crashes clang on some bots.
      else:
        self.m.chromium.apply_config('trybot_flavor')

  @property
  def should_build(self):
    return self.bot_type in ('builder', 'builder_tester')

  @property
  def should_use_reclient(self):
    return self.buildername not in _LOCAL_COMPILE_BUILDERS

  @property
  def should_test(self):
    return self.bot_type in ('tester', 'builder_tester')

  @property
  def should_upload_build(self):
    return self.bot_config.get('triggers')

  @property
  def should_download_build(self):
    return self.bot_config.get('parent_buildername')

  def checkout(self):
    with self.m.context(cwd=self.m.chromium_checkout.checkout_dir):
      update_result = self.m.bot_update.ensure_checkout()
      self.revision = update_result.properties['got_revision']

  @contextlib.contextmanager
  def ensure_sdk(self):
    if 'ensure_sdk' in self.bot_config:
      with self.m.osx_sdk(self.bot_config['ensure_sdk']):
        yield
    else:
      yield

  def maybe_trigger(self):
    triggers = self.bot_config.get('triggers')
    properties = {
      'revision': self.revision,
      'parent_got_revision': self.revision,
      'parent_buildername': self.m.buildbucket.builder_name,
    }
    if triggers:
      self.m.scheduler.emit_trigger(
          self.m.scheduler.BuildbucketTrigger(properties=properties),
          project='libyuv', jobs=triggers)


  def package_build(self):
    upload_url = self.m.archive.legacy_upload_url(
        self.group_config.get('build_gs_bucket'),
        extra_url_components=self.m.builder_group.for_current)
    self.m.archive.zip_and_upload_build(
        'package build',
        self.m.chromium.c.build_config_fs,
        upload_url,
        build_revision=self.revision)

  def extract_build(self):
    if not self.m.properties.get('parent_got_revision'):
      raise self.m.step.StepFailure(
         'Testers cannot be forced without providing revision information. '
         'Please select a previous build and click [Rebuild] or force a build '
         'for a Builder instead (will trigger new runs for the testers).')

    # Ensure old build directory isn't being used by removing it.
    self.m.file.rmtree(
        'build directory',
        self.m.chromium.c.build_dir / self.m.chromium.c.build_config_fs)

    download_url = self.m.archive.legacy_download_url(
        self.group_config.get('build_gs_bucket'),
        extra_url_components=self.m.builder_group.for_current)
    self.m.archive.download_and_unzip_build(
        'extract build',
        self.m.chromium.c.build_config_fs,
        download_url,
        build_revision=self.revision)

  def runtests(self):
    """Add a suite of test steps."""
    with self.m.context(cwd=self.m.chromium_checkout.checkout_dir):
      with self.m.defer.context() as defer:
        if self.m.chromium.c.TARGET_PLATFORM == 'android':
          defer(self.m.chromium_android.common_tests_setup_steps)
          defer(self.m.chromium_android.run_test_suite, 'libyuv_unittest')
          defer(self.m.chromium_android.shutdown_device_monitor)
          defer(self.m.chromium_android.logcat_dump)
          defer(self.m.chromium_android.stack_tool_steps,
                force_latest_version=True)
        else:
          # Ignoring --no-sandbox because libyuv uses absl/flags which
          # raises an error when flags are unknown to the binary.
          # This is fine, since these tests are not sandbox aware, it is
          # just self.m.chromium.runtest that adds the flag.
          defer(
              self.m.chromium.runtest,
              'libyuv_unittest',
              args=['--undefok=no-sandbox'],
          )
