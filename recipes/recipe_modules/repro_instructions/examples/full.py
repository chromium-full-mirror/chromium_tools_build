# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import (instruction as
                                                       instruction_pb)

DEPS = [
    'repro_instructions',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/path',
    'recipe_engine/resultdb',
    'recipe_engine/step',
]


def RunSteps(api):
  api.repro_instructions.source_dir = api.path.cache_dir / 'builder' / 'src'
  _ = api.repro_instructions.source_dir
  api.repro_instructions.build_dir = (
      api.path.cache_dir / 'builder' / 'src' / 'out' / 'Release')
  _ = api.repro_instructions.build_dir

  mock_test = steps.MockTestSpec.create('mock test').get_test(api)

  utr_content = api.repro_instructions.get_utr_instruction(
      'run', [mock_test.name], extra_args=['filter with " in it'])
  api.assertions.assertIn('"filter with \\" in it"', utr_content)
  api.assertions.assertIn('"mock test"', utr_content)
  api.repro_instructions.get_utr_instruction('compile', [])

  api.repro_instructions.update_invocation_instructions()

  api.repro_instructions.create_step_instruction('tag', 'description',
                                                 'run foo.bar locally',
                                                 'run foo.bar remotely')
  api.repro_instructions.create_test_result_instruction('tag', 'description',
                                                        'run foo.bar locally',
                                                        'run foo.bar remotely',
                                                        ['invocation-id'])


def GenTests(api):
  yield api.test(
      'full',
      api.buildbucket.generic_build(builder='fake builder'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'rdb_disabled',
      api.post_process(post_process.DropExpectation),
  )
