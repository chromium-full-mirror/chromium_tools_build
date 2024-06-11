# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.engine_types import freeze
from recipe_engine.recipe_api import Property
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

DEPS = [
    'cronet',
    'chromium',
    'recipe_engine/buildbucket',
    'recipe_engine/path',
    'recipe_engine/properties',
    'reclient',
]

BUILDERS = freeze({
    'local_test': {
        'recipe_config': 'main_builder_mb',
        'kwargs': {
            'BUILD_CONFIG': 'Debug',
        },
        'use_reclient': False,
    },
})


def RunSteps(api):
  builder_config = BUILDERS.get(api.buildbucket.builder_name, {})
  recipe_config = builder_config['recipe_config']
  kwargs = builder_config.get('kwargs', {})

  use_reclient = builder_config.get('use_reclient', True)

  api.cronet.init_and_sync(
      recipe_config, kwargs,
      chromium_apply_config=builder_config.get('chromium_apply_config'))

  raw_result = api.cronet.build(use_reclient=use_reclient)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result


def _sanitize_nonalpha(text):
  return ''.join(c if c.isalnum() else '_' for c in text.lower())


def GenTests(api):
  for builder in BUILDERS.keys():
    yield api.test(
        _sanitize_nonalpha(builder),
        api.chromium.ci_build(
            builder=builder,
            builder_group='chromium.android',
        ),
        api.reclient.properties(),
    )

  yield api.test(
      'compile_failure',
      api.chromium.ci_build(
          builder='local_test',
          builder_group='chromium.android',
      ),
      api.reclient.properties(),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
