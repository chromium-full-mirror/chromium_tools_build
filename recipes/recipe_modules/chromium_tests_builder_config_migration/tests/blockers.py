# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import textwrap

from typing import Optional

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build import (chromium_tests_builder_config_migration as
                                  ctbcm)
from RECIPE_MODULES.build.chromium import BuilderId

from PB.recipe_modules.build.chromium_tests_builder_config_migration import (
    properties as properties_pb)

DEPS = [
    'chromium_tests_builder_config',
    'chromium_tests_builder_config_migration',
    'recipe_engine/properties',
]

PROPERTIES = properties_pb.InputProperties


class _NonExistentBuilder(ctbcm.BlockerCategory):

  _NON_EXISTENT_BUILDERS = (BuilderId.create_for_group('fake-group',
                                                       'non-existent-builder'),)

  def get_blocker(
      self,
      builder_id: BuilderId,
      builder_spec: ctbc.BuilderSpec,
  ) -> Optional[str]:
    del builder_spec
    if builder_id not in self._NON_EXISTENT_BUILDERS:
      return None
    return f"builder '{builder_id}' does not exist"


def RunSteps(api, properties):
  ctbc_api = api.chromium_tests_builder_config
  return api.chromium_tests_builder_config_migration(
      properties,
      ctbc_api.builder_db,
      ctbc_api.try_db,
      additional_blocker_categories=[_NonExistentBuilder()])


def GenTests(api):
  expected_non_existent_groupings = textwrap.dedent("""\
      {
        "fake-group:fake-builder": {
          "builders": [
            "fake-group:fake-builder"
          ]
        },
        "fake-group:non-existent-builder": {
          "blockers": [
            "builder 'fake-group:non-existent-builder' does not exist"
          ],
          "builders": [
            "fake-group:non-existent-builder"
          ]
        }
      }""")

  yield api.test(
      'blocker-groupings',
      api.properties(
          groupings_operation={
              'output_path':
                  '/fake/output/path',
              'builder_group_filters': [
                  {
                      'builder_group_regex': 'fake-group',
                  },
              ],
          }),
      api.chromium_tests_builder_config.databases(
          ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder': ctbc.BuilderSpec.create(),
                  'non-existent-builder': ctbc.BuilderSpec.create(),
              },
          }),
          ctbc.TryDatabase.create({}),
      ),
      api.post_check(post_process.StatusSuccess),
      api.post_check(lambda check, steps: \
          check(expected_non_existent_groupings in steps['groupings'].cmd)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'blocker-migration',
      api.properties(
          migration_operation={
              'output_path':
                  '/fake/output/path',
              'builders_to_migrate': [{
                  'builder_group': 'fake-group',
                  'builder': 'non-existent-builder',
              }],
          }),
      api.chromium_tests_builder_config.databases(
          ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-builder': ctbc.BuilderSpec.create(),
                  'non-existent-builder': ctbc.BuilderSpec.create(),
              },
          }),
          ctbc.TryDatabase.create({}),
      ),
      api.post_check(post_process.StatusException),
      api.post_check(
          post_process.ResultReasonRE,
          "The grouping for 'fake-group:non-existent-builder' cannot be migrated"
      ),
      api.post_process(post_process.DropExpectation),
  )
