# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.engine_types import freeze
from recipe_engine import post_process
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    chromium,
    chromium_android,
    cronet,
    siso,
)
from RECIPE_MODULES.depot_tools import bot_update, gclient
from RECIPE_MODULES.recipe_engine import buildbucket, properties, runtime


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_android: chromium_android.API
  cronet: cronet.API
  gclient: gclient.API
  properties: properties.API
  runtime: runtime.API
  siso: siso.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  chromium: chromium.TEST_API
  properties: properties.TEST_API
  runtime: runtime.TEST_API
  siso: siso.TEST_API

BUILDERS = freeze({
    'local_test': {
        'kwargs': {
            'BUILD_CONFIG': 'Debug',
            'TARGET_ARCH': 'arm',
            'TARGET_BITS': 32,
            'TARGET_PLATFORM': 'android',
        },
        'use_remoteexec': False,
    },
    'gn_test': {
        'kwargs': {
            'BUILD_CONFIG': 'Debug',
            'TARGET_ARCH': 'arm',
            'TARGET_BITS': 32,
            'TARGET_PLATFORM': 'android',
        },
        'chromium_apply_config': ['gn'],
    },
    'mb_test': {
        'kwargs': {
            'BUILD_CONFIG': 'Release',
            'TARGET_ARCH': 'arm',
            'TARGET_BITS': 32,
            'TARGET_PLATFORM': 'android',
        },
        'chromium_apply_config': ['mb'],
    },
})


def RunSteps(api: DEPS):
  builder_config = BUILDERS.get(api.buildbucket.builder_name, {})
  kwargs = builder_config.get('kwargs', {})
  chromium_apply_config = builder_config.get('chromium_apply_config', [])

  gclient_cfg = api.gclient.make_config('chromium_bare')
  gclient_cfg.target_os = ['android']

  api.chromium.set_config('main_builder', **kwargs)
  api.chromium.apply_config('cronet_builder')
  for c in chromium_apply_config:
    api.chromium.apply_config(c)

  api.chromium_android.set_config('base_config')
  api.chromium_android.apply_config('use_devil_provision')

  update_result = api.bot_update.ensure_checkout(gclient_cfg)
  source_dir = update_result.source_root.path

  cronet = api.cronet
  cronet.generate_changelist(source_dir)

  raw_result = cronet.build(source_dir)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  return cronet.upload_package(source_dir, kwargs['BUILD_CONFIG'])


def GenTests(api: TEST_DEPS):
  for builder in BUILDERS:
    for is_experimental in (False, True):
      test_name = builder
      if is_experimental:
        test_name += '_experimental'
      yield api.test(
          test_name,
          api.siso.properties(),
          api.chromium.ci_build(
              builder_group='fake-group',
              builder=builder,
          ),
          api.runtime(is_experimental=is_experimental),
      )

  # Do these proerties actually ever get set anymore?
  yield api.test(
      'optional_properties',
      api.siso.properties(),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='local_test',
          revision='a' * 40,
      ),
      api.properties(
          git_revision='a' * 40,
          got_revision_cp=api.bot_update.gen_commit_position('src'),
      ),
  )

  yield api.test(
      'compile_failure',
      api.siso.properties(),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='local_test',
      ),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
