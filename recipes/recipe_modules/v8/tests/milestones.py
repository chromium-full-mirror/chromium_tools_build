# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'recipe_engine/step',
    'v8',
]


def RunSteps(api):
  # Test infer_active_branches
  definitions = 'ACTIVE_BRANCHES = ["15.1", "15.0"]'
  branches = api.v8.infer_active_branches(definitions)
  api.step(f'Inferred branches: {", ".join(branches)}', [])

  # Test infer_active_branches with missing ACTIVE_BRANCHES
  definitions_missing = 'SOME_OTHER_VAR = []'
  branches_missing = api.v8.infer_active_branches(definitions_missing)
  api.step(f'Missing branches: {len(branches_missing)}', [])

  # Test calculate_versions
  definitions_versions = 'versions = {"beta": "15.1", "stable": "15.0", "extended": "14.9"}'
  new_v_definitions = api.v8.calculate_versions(definitions_versions, 152)
  api.step('Calculated versions', [])
  api.step.active_result.presentation.logs['definitions'] = new_v_definitions

  # Test update_active_branches
  new_definitions = api.v8.update_active_branches(["15.2", "15.1"])
  api.step('New definitions', [])
  api.step.active_result.presentation.logs['definitions'] = new_definitions


def GenTests(api):
  yield api.test('basic')
