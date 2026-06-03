# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec

DEPS = [
    'chromium',
    'chromium_rts',
    'chromium_tests_builder_config',
    'recipe_engine/cv',
    'recipe_engine/json',
    'recipe_engine/properties',
]

PROPERTIES = {
    'builder_rts_selection': Property(kind=str, default=try_spec.NEVER),
}


def RunSteps(api, builder_rts_selection):
  api.chromium_rts.rts_model = 'chromium-rts'
  assert api.chromium_rts.rts_model == 'chromium-rts'

  builder_config = ctbc.TrySpec.create(
      mirrors=[
          ctbc.TryMirror.create(
              builder_group='fake-group',
              buildername='fake-tester',
              tester='fake-tester',
          ),
      ],
      regression_test_selection=builder_rts_selection,
  )
  api.chromium_rts.init_rts_options(builder_config)


def GenTests(api):
  yield api.test(
      'basic',
      api.properties(builder_rts_selection=try_spec.ALWAYS),
      api.cv(run_mode='FULL_RUN', top_level=True),
      api.chromium.try_build(),
      api.step_data(
          api.json.output([{
              "approvals": {
                  "Auto-Submit": " 0",
                  "Commit-Queue": " 0"
              },
              "_account_id": 1111,
              "name": "Author Person",
              "email": "someone@chromium.org"
          }, {
              "approvals": {
                  "Code-Review": " 0",
                  "Commit-Queue": " 0"
              },
              "_account_id": 222,
              "name": "Reviewer Person",
              "email": "someoneelse@chromium.org"
          }])),
      api.post_process(post_process.MustRun, 'rts options'),
      api.post_process(post_process.PropertyEquals, 'rts_model',
                       'chromium-rts'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_on_dry_run',
      api.properties(builder_rts_selection=try_spec.ALWAYS),
      api.chromium.try_build(experiments=['chromium_rts.rts'],),
      api.cv(run_mode='DRY_RUN', top_level=True),
      api.post_process(post_process.MustRun, 'rts options'),
      api.post_process(post_process.PropertyEquals, 'rts_model',
                       'chromium-rts'),
      api.post_process(post_process.DropExpectation),
  )
