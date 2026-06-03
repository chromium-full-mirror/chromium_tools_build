# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'chromium',
    'chromium_rts',
    'chromium_tests',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
]
from PB.recipe_modules.build.chromium_compilator.properties import InputProperties
from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps

_ORCHESTRATOR_BUILD_ID = 1234
_COMPILATOR_BUILD_ID = 5678


def RunSteps(api):
  supports_rts = api.properties.get('supports_rts', True)
  tests = [
      steps.MockTestSpec.create('MockTest', supports_rts=supports_rts).get_test(
          api.chromium_tests),
  ]
  assert (not api.m.chromium_rts.enabled)
  api.m.chromium_rts.rts_model = 'chromium-rts'
  assert (api.m.chromium_rts.enabled)

  mb_args = api.m.chromium_rts.mb_args()
  assert (mb_args[0] == '--rts-model')
  assert (mb_args[1] == 'chromium-rts')
  assert (len(mb_args) == 2)

  api.step.empty('executor build id: %s' %
                 api.m.chromium_rts.test_executor_build_id)

  api.m.chromium_rts.setup_tests(tests)
  if supports_rts:
    assert (tests[0].is_rts)
  api.m.chromium_rts.generate_filter_files(api.path.cleanup_dir,
                                           api.path.cleanup_dir, tests)

def GenTests(api):
  yield api.test(
      'rts_basic',
      api.chromium.try_build(
          builder='linux-rel',
          experiments=['chromium_rts.rts'],
          build_id=_COMPILATOR_BUILD_ID),
      api.post_process(post_process.MustRun, 'executor build id: 5678'),
      api.post_process(post_process.MustRun, 'RTS was used'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_orchestrator',
      api.chromium.try_build(
          builder='linux-rel',
          experiments=['chromium_rts.rts'],
          build_id=_COMPILATOR_BUILD_ID,
          ancestor_ids=[_ORCHESTRATOR_BUILD_ID]),
      api.properties(
          InputProperties(
              orchestrator=InputProperties.Orchestrator(
                  builder_name='linux-rel', builder_group='fake-try-group'))),
      api.post_process(post_process.MustRun, 'executor build id: 1234'),
      api.post_process(post_process.MustRun, 'RTS was used'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip_test_selection',
      api.chromium.try_build(
          experiments=['chromium_rts.rts'], build_id=_COMPILATOR_BUILD_ID),
      api.properties(supports_rts=False),
      api.post_process(post_process.DoesNotRun, 'RTS was used'),
      api.post_process(post_process.DropExpectation),
  )
