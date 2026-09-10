# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import contextlib

from recipe_engine import recipe_api
from recipe_engine.config_types import Path

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
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self.bot_config = None
    self.bot_type = None
    self.buildername = None
    self.revision = ''

  def apply_bot_config(self, builder_id, builder_config):
    self.bot_config = libyuv_builders.BUILDERS_DB.builders_by_group[
      builder_id.group
    ][builder_id.builder]
    self.bot_type = self.bot_config.bot_type
    self.buildername = builder_id.builder
    self.m.chromium_tests.configure_build(builder_config)
    if self.m.tryserver.is_tryserver:
      self.m.chromium.apply_config('trybot_flavor')

  @property
  def should_build(self):
    return self.bot_type in ('builder', 'builder_tester')

  @property
  def should_use_remoteexec(self):
    return self.buildername not in _LOCAL_COMPILE_BUILDERS

  @property
  def should_test(self):
    return self.bot_type in ('tester', 'builder_tester')

  @property
  def should_upload_build(self):
    return self.bot_config.triggers

  @property
  def should_download_build(self):
    return self.bot_config.parent_buildername

  def checkout(self):
    with self.m.context(cwd=self.m.chromium_checkout.default_checkout_dir):
      update_result = self.m.bot_update.ensure_checkout()
      self.m.chromium_checkout.set_paths_from_update_result(update_result)
      self.revision = update_result.properties['got_revision']
    return update_result

  @contextlib.contextmanager
  def ensure_sdk(self):
    if self.bot_config.ensure_sdk:
      with self.m.osx_sdk(self.bot_config.ensure_sdk):
        yield
    else:
      yield

  def maybe_trigger(self):
    triggers = self.bot_config.triggers
    properties = {
      'revision': self.revision,
      'parent_got_revision': self.revision,
      'parent_buildername': self.m.buildbucket.builder_name,
    }
    if triggers:
      self.m.scheduler.emit_trigger(
        self.m.scheduler.BuildbucketTrigger(properties=properties),
        project='libyuv',
        jobs=[triggers],
      )

  def package_build(self, source_dir: Path):
    upload_url = self.m.archive.legacy_upload_url(
      'chromium-libyuv', extra_url_components=self.m.builder_group.for_current
    )
    self.m.archive.zip_and_upload_build(
      'package build',
      self.m.chromium.c.build_config_fs,
      source_dir,
      build_url=upload_url,
      build_revision=self.revision,
    )

  def extract_build(self, source_dir: Path, build_dir: Path):
    if not self.m.properties.get('parent_got_revision'):
      raise self.m.step.StepFailure(
        'Testers cannot be forced without providing revision information. '
        'Please select a previous build and click [Rebuild] or force a build '
        'for a Builder instead (will trigger new runs for the testers).'
      )

    # Ensure old build directory isn't being used by removing it.
    self.m.file.rmtree('build directory', build_dir)

    download_url = self.m.archive.legacy_download_url(
      'chromium-libyuv', extra_url_components=self.m.builder_group.for_current
    )
    self.m.archive.download_and_unzip_build(
      'extract build',
      self.m.chromium.c.build_config_fs,
      download_url,
      source_dir,
      build_revision=self.revision,
    )
    # The zip_and_upload_build step ignores files in obj folder
    # but it is the binary in obj that is executed.
    self.m.step(
      'mkdir', ['mkdir', '-p', build_dir / 'exe.unstripped/obj/libyuv_unittest']
    )
    self.m.step(
      'cp exe',
      [
        'cp',
        build_dir / 'libyuv_unittest',
        build_dir / 'exe.unstripped/obj/libyuv_unittest',
      ],
    )

  def runtests(self, build_dir: Path):
    """Add a suite of test steps."""
    with self.m.context(cwd=self.m.chromium_checkout.checkout_dir):
      with self.m.defer.context() as defer:
        if self.m.chromium.c.TARGET_PLATFORM == 'android':
          source_dir = self.m.chromium_checkout.source_dir

          # Below code is a copy of chromium_android.common_tests_setup_steps
          # without some steps that are failing or flayky in libyuv.
          defer(self.m.chromium_android.create_adb_symlink, source_dir)
          defer(self.m.chromium_android.spawn_logcat_monitor, source_dir)
          defer(self.m.chromium_android.spawn_device_monitor, source_dir)
          defer(self.m.chromium_android.authorize_adb_devices, source_dir)

          defer(
            self.m.chromium_android.run_test_suite,
            source_dir,
            build_dir,
            'libyuv_unittest',
          )
          defer(self.m.chromium_android.shutdown_device_monitor)
          defer(self.m.chromium_android.logcat_dump, source_dir, build_dir)
          defer(
            self.m.chromium_android.stack_tool_steps,
            source_dir,
            force_latest_version=True,
          )
        else:
          # Ignoring --no-sandbox because libyuv uses absl/flags which
          # raises an error when flags are unknown to the binary.
          # This is fine, since these tests are not sandbox aware, it is
          # just self.m.chromium.runtest that adds the flag.
          defer(
            self.m.chromium.runtest,
            self.m.chromium_checkout.checkout_dir,
            build_dir,
            'libyuv_unittest',
            args=['--undefok=no-sandbox'],
          )
