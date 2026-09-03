# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for building and running tests for Libyuv stand-alone.
"""

from recipe_engine import post_process
from recipe_engine.engine_types import freeze
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build.libyuv import builders

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    builder_group,
    chromium,
    chromium_android,
    chromium_tests_builder_config,
    libyuv,
    siso,
)
from RECIPE_MODULES.depot_tools import bot_update, gclient, tryserver
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    path,
    platform,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_android: chromium_android.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  context: context.API
  gclient: gclient.API
  libyuv: libyuv.API
  path: path.API
  platform: platform.API
  properties: properties.API
  siso: siso.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  builder_group: builder_group.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  siso: siso.TEST_API


def RunSteps(api: DEPS):
  libyuv = api.libyuv
  builder_id, builder_config = api.chromium_tests_builder_config.lookup_builder(
      builder_db=builders.BUILDERS_DB)
  libyuv.apply_bot_config(builder_id, builder_config)

  api.siso.enable_download_remoteexec_cfg_hook()
  update_result = libyuv.checkout()
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  api.chromium.runhooks(source_dir, build_dir)

  if libyuv.should_build:
    with libyuv.ensure_sdk(), api.chromium.guard_compile(build_dir):
      api.chromium.c.gn_args.append('use_siso=true')
      api.chromium.run_gn(
          source_dir, build_dir, use_remoteexec=libyuv.should_use_remoteexec)
      raw_result = api.chromium.compile(source_dir, build_dir)
      if raw_result.status != common_pb.SUCCESS:
        return raw_result
    if libyuv.should_upload_build:
      libyuv.package_build(source_dir)

  if libyuv.should_download_build:
    libyuv.extract_build(source_dir, build_dir)

  if libyuv.should_test:
    libyuv.runtests(build_dir)

  libyuv.maybe_trigger()

def _sanitize_nonalpha(text):
  return ''.join(c if c.isalnum() else '_' for c in text.lower())


def GenTests(api: TEST_DEPS):
  def generate_builder(builder_group, buildername, revision, suffix=None):
    suffix = suffix or ''
    bot_config = builders.BUILDERS_DB.builders_by_group[builder_group][
        buildername]
    bot_type = bot_config.bot_type

    chromium_kwargs = bot_config.chromium_config_kwargs
    test = api.test('%s_%s%s' % (_sanitize_nonalpha(builder_group),
                                 _sanitize_nonalpha(buildername), suffix))

    if builder_group.startswith('tryserver'):
      test += api.buildbucket.try_build(
          project='libyuv',
          builder=buildername,
          build_number=1337,
          git_repo='https://chromium.googlesource.com/libyuv/libyuv',
          revision=revision,
          change_number=456789,
          patch_set=12)
    else:
      test += api.buildbucket.ci_build(
          project='libyuv',
          builder=buildername,
          build_number=1337,
          git_repo='https://chromium.googlesource.com/libyuv/libyuv',
          revision=revision)

    test += api.siso.properties()
    test += api.builder_group.for_current(builder_group)
    test += api.properties(
        buildername=buildername,
        bot_id='bot_id',
        BUILD_CONFIG=chromium_kwargs['BUILD_CONFIG'])
    test += api.platform(bot_config.simulation_platform,
                         chromium_kwargs.get('TARGET_BITS', 64))

    if bot_config.parent_buildername:
      test += api.properties(parent_buildername=bot_config.parent_buildername)

    if bot_type == 'tester':
      test += api.properties(parent_got_revision=revision)

    test += api.properties(buildnumber=1337)
    return test

  for builder_group, group_config in builders.BUILDERS_DB.builders_by_group.items(
  ):
    for buildername in group_config.keys():
      yield generate_builder(builder_group, buildername, revision='a' * 40)

  # Forced builds (not specifying any revision) and test failures.
  builder_group = 'client.libyuv'
  yield generate_builder(
      builder_group, 'Linux64 Debug', revision=None, suffix='_forced')
  yield generate_builder(
      builder_group, 'Android Debug', revision=None, suffix='_forced')
  yield generate_builder(
      builder_group,
      'Android Tester ARM32 Debug (Nexus 5X)',
      revision=None,
      suffix='_forced_invalid') + api.expect_status('FAILURE')
  yield generate_builder(
      builder_group, 'iOS Debug', revision=None, suffix='_forced')

  yield generate_builder('tryserver.libyuv', 'linux', revision=None,
                         suffix='_forced')

  yield (generate_builder(
      'tryserver.libyuv', 'linux', revision=None, suffix='_compile_failed') +
         api.step_data('compile', retcode=1) + api.expect_status('FAILURE') +
         api.post_process(post_process.DropExpectation))
