# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api


class ChromiumTestApi(recipe_test_api.RecipeTestApi):
  @recipe_test_api.mod_test_data
  @staticmethod
  def change_char_size_limit(size_limit):
    """Returns an integer that limits compile failure format size.

    This controls how many characters the compile failure summary can have.
    """
    return size_limit

  @recipe_test_api.mod_test_data
  @staticmethod
  def change_line_limit(line_limit):
    """Returns an integer that limits compile failure line size

    This controls how many characters each line in
    the compile failure summary can have.
    """
    return line_limit

  def _common_test_data(
    self,
    bot_id,
    default_builder_group,
    builder_group=None,
    parent_builder_group=None,
    parent_buildername=None,
    parent_build_id=1337,
  ):
    test_data = self.m.properties(bot_id=bot_id)
    builder_group = builder_group or default_builder_group
    if builder_group is not None:
      test_data += self.m.builder_group.for_current(builder_group)
    if parent_buildername is not None:
      parent_builder_group = parent_builder_group or builder_group
      test_data += self.m.builder_group.for_parent(parent_builder_group)
      test_data += self.m.properties(
        parent_buildername=parent_buildername, parent_build_id=parent_build_id
      )
    return test_data

  def ci_build(
    self,
    project='chromium',
    bucket='ci',
    builder_group=None,
    builder='Linux Builder',
    parent_builder_group=None,
    parent_buildername=None,
    bot_id='test_bot',
    git_repo='https://chromium.googlesource.com/chromium/src',
    revision='2d72510e447ab60a9728aeea2362d8be2cbd7789',
    build_number=571,
    tags=None,
    **kwargs,
  ):
    """Create test data for a chromium CI build.

    Adding this to a test will set properties and inputs in a manner
    that is compatible with the chromium module for builds that would be
    triggered by a scheduler poller with an associated gitiles commit
    (or triggered by another builder that was triggered by a scheduler
    poller).
    """
    test_data = self._common_test_data(
      bot_id=bot_id,
      default_builder_group='chromium.linux',
      builder_group=builder_group,
      parent_builder_group=parent_builder_group,
      parent_buildername=parent_buildername,
    )
    return test_data + self.m.buildbucket.ci_build(
      project=project,
      bucket=bucket,
      builder=builder,
      build_number=build_number,
      revision=revision,
      git_repo=git_repo,
      tags=tags,
      **kwargs,
    )

  def properties(
    self,
    xcode_build_version=None,
    fail_build_on_clang_warnings=None,
    mac_toolchain_version=None,
  ):
    properties = {}
    if xcode_build_version is not None:
      properties['xcode_build_version'] = xcode_build_version
    if fail_build_on_clang_warnings is not None:
      properties['fail_build_on_clang_warnings'] = fail_build_on_clang_warnings
    if mac_toolchain_version is not None:
      properties['mac_toolchain_version'] = mac_toolchain_version
    return self.m.properties(**{'$build/chromium': properties})

  def generic_build(
    self,
    project='chromium',
    bucket='ci',
    builder_group=None,
    builder='Linux Builder',
    parent_builder_group=None,
    parent_buildername=None,
    bot_id='test_bot',
    build_number=571,
    tags=None,
    **kwargs,
  ):
    """Create test data for a generic chromium build.

    Adding this to a test will set properties and inputs in a manner
    that is compatible with the chromium module for builds that have
    neither an associated gitiles commit or gerrit change (e.g. CI
    builder triggered via the scheduler UI or a cron-like schedule).
    """
    test_data = self._common_test_data(
      bot_id=bot_id,
      default_builder_group='chromium.linux',
      builder_group=builder_group,
      parent_builder_group=parent_builder_group,
      parent_buildername=parent_buildername,
    )
    return test_data + self.m.buildbucket.generic_build(
      project=project,
      bucket=bucket,
      builder=builder,
      build_number=build_number,
      tags=tags,
      **kwargs,
    )

  def try_build(
    self,
    project='chromium',
    bucket='try',
    builder_group=None,
    builder='linux-rel',
    bot_id='test_bot',
    git_repo='https://chromium.googlesource.com/chromium/src',
    build_number=571,
    change_number=456789,
    patch_set=12,
    tags=None,
    **kwargs,
  ):
    """Create test data for a chromium try build.

    Adding this to a test will set properties and inputs in a manner
    that is compatible with the chromium module for try builds with an
    associated gerrit change.
    """
    test_data = self._common_test_data(
      bot_id=bot_id,
      default_builder_group='tryserver.chromium.linux',
      builder_group=builder_group,
    )
    return test_data + self.m.buildbucket.try_build(
      project=project,
      bucket=bucket,
      builder=builder,
      build_number=build_number,
      git_repo=git_repo,
      change_number=change_number,
      patch_set=patch_set,
      tags=tags,
      **kwargs,
    )

  def override_version(
    self, major=64, minor=0, build=3282, patch=0, step_name='get version'
  ):
    assert isinstance(major, int)
    assert isinstance(minor, int)
    assert isinstance(build, int)
    assert isinstance(patch, int)
    assert isinstance(step_name, str)
    version_file_contents = 'MAJOR=%d\nMINOR=%d\nBUILD=%d\nPATCH=%d\n' % (
      major,
      minor,
      build,
      patch,
    )
    return self.override_step_data(
      step_name, self.m.file.read_text(version_file_contents)
    )

  def gen_tests_for_builders(
    self,
    builder_dict,
    project='chromium',
    git_repo='https://chromium.googlesource.com/chromium/src',
  ):
    # TODO: crbug.com/354674. Figure out where to put "simulation"
    # tests. Is this really the right place?

    def _sanitize_nonalpha(text):
      return ''.join(c if c.isalnum() else '_' for c in text)

    for builder_group in builder_dict:
      for buildername in builder_dict[builder_group]['builders']:
        properties = self.m.properties()
        if 'mac' in buildername or 'Mac' in buildername:
          platform_name = 'mac'
          properties = self.m.properties(
            xcode_build_version='fake-xcode-version'
          )
        elif 'win' in buildername or 'Win' in buildername:
          platform_name = 'win'
        else:
          platform_name = 'linux'
        test = self.test(
          'full_%s_%s'
          % (
            _sanitize_nonalpha(builder_group),
            _sanitize_nonalpha(buildername),
          ),
          self.m.platform.name(platform_name),
          properties,
        )
        if builder_group.startswith('tryserver'):
          test += self.try_build(
            project=project,
            builder_group=builder_group,
            builder=buildername,
            git_repo=git_repo,
          )
        else:
          test += self.ci_build(
            project=project,
            builder_group=builder_group,
            builder=buildername,
            git_repo=git_repo,
          )

        yield test
