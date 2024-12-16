# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

DEPS = [
    'chromium',
    'chromium_rts',
    'recipe_engine/json',
    'recipe_engine/properties',
]

PROPERTIES = {
    'expected_disable': Property(kind=bool, default=True),
}


def RunSteps(api, expected_disable):
  disabled = api.chromium_rts._is_rts_footer_disabled()
  assert expected_disable == disabled


def GenTests(api):
  yield api.test(
      'rts_disabled',
      api.properties(expected_disable=True),
      api.chromium.try_build(),
      api.step_data('parse description',
                    api.json.output({'Disable-Rts': ['true']})),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_enabled',
      api.properties(expected_disable=False),
      api.chromium.try_build(),
      api.step_data('parse description',
                    api.json.output({'Disable-Rts': ['false']})),
      api.post_process(post_process.DropExpectation),
  )
