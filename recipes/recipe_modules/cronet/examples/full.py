# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.engine_types import freeze
from recipe_engine import post_process
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

DEPS = [
    'chromium',
    'cronet',
    'depot_tools/bot_update',
    'recipe_engine/buildbucket',
    'recipe_engine/runtime',
    'recipe_engine/properties',
]

BUILDERS = freeze({
  'local_test': {
    'recipe_config': 'main_builder',
    'upload_package': True,
    'kwargs': {
      'BUILD_CONFIG': 'Debug',
    },
    'use_goma': False,
  },
  'gn_test': {
    'recipe_config': 'main_builder',
    'upload_package': True,
    'kwargs': {
      'BUILD_CONFIG': 'Debug',
    },
    'chromium_apply_config': ['gn'],
  },
  'mb_test': {
    'recipe_config': 'main_builder',
    'upload_package': True,
    'kwargs': {
      'BUILD_CONFIG': 'Release',
    },
    'chromium_apply_config': ['mb'],
  },
})


def RunSteps(api):
  builder_config = BUILDERS.get(api.buildbucket.builder_name, {})
  recipe_config = builder_config['recipe_config']
  kwargs = builder_config.get('kwargs', {})
  chromium_apply_config = builder_config.get('chromium_apply_config')

  cronet = api.cronet
  cronet.init_and_sync(recipe_config, kwargs,
                       chromium_apply_config=chromium_apply_config)
  cronet.generate_changelist()

  use_goma = builder_config.get('use_goma', True)
  raw_result = cronet.build(use_goma=use_goma)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  return cronet.upload_package(kwargs['BUILD_CONFIG'])


def GenTests(api):
  for builder in BUILDERS:
    for is_experimental in (False, True):
      test_name = builder
      if is_experimental:
        test_name += '_experimental'
      yield api.test(
          test_name,
          api.chromium.ci_build(
              builder_group='fake-group',
              builder=builder,
          ),
          api.runtime(is_experimental=is_experimental),
      )

  # Do these proerties actually ever get set anymore?
  yield api.test(
      'optional_properties',
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
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='local_test',
      ),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
