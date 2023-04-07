# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe providing src-side builder config migration functionality.

This recipe is not used on any builder, it is used as part of a PRESUBMIT check
to prevent additional configs from being added to the recipe.
"""
# TODO(crbug.com/868153) Remove this once the builder config migration is
# complete

import textwrap

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium import BuilderId

from PB.recipe_modules.build.chromium_tests_builder_config_migration import (
    properties as properties_pb)

PROPERTIES = properties_pb.InputProperties

DEPS = [
    'chromium_tests_builder_config',
    'chromium_tests_builder_config_migration',
    'recipe_engine/properties',
]


def RunSteps(api, properties):
  ctbc_api = api.chromium_tests_builder_config
  return api.chromium_tests_builder_config_migration(properties,
                                                     ctbc_api.builder_db,
                                                     ctbc_api.try_db)


def GenTests(api):
  expected_groupings = textwrap.dedent("""\
      {
        "migration.testing:bar": {
          "builders": [
            "migration.testing:bar",
            "migration.testing:bar-tests",
            "tryserver.migration.testing:bar"
          ]
        },
        "migration.testing:bar-tests": {
          "builders": [
            "migration.testing:bar",
            "migration.testing:bar-tests",
            "tryserver.migration.testing:bar"
          ]
        },
        "migration.testing:foo": {
          "builders": [
            "migration.testing:foo",
            "migration.testing:foo-x-tests",
            "migration.testing:foo-y-tests",
            "tryserver.migration.testing:foo"
          ]
        },
        "migration.testing:foo-x-tests": {
          "builders": [
            "migration.testing:foo",
            "migration.testing:foo-x-tests",
            "migration.testing:foo-y-tests",
            "tryserver.migration.testing:foo"
          ]
        },
        "migration.testing:foo-y-tests": {
          "builders": [
            "migration.testing:foo",
            "migration.testing:foo-x-tests",
            "migration.testing:foo-y-tests",
            "tryserver.migration.testing:foo"
          ]
        },
        "tryserver.migration.testing:bar": {
          "builders": [
            "migration.testing:bar",
            "migration.testing:bar-tests",
            "tryserver.migration.testing:bar"
          ]
        },
        "tryserver.migration.testing:foo": {
          "builders": [
            "migration.testing:foo",
            "migration.testing:foo-x-tests",
            "migration.testing:foo-y-tests",
            "tryserver.migration.testing:foo"
          ]
        }
      }""")

  yield api.test(
      'groupings',
      api.properties(
          groupings_operation={
              'output_path':
                  '/fake/output/path',
              'builder_group_filters': [
                  {
                      'builder_group_regex': r'(tryserver\.)?migration(\..+)?',
                  },
              ],
          }),
      api.post_check(lambda check, steps: \
          check(expected_groupings in steps['groupings'].cmd)),
      api.post_process(post_process.DropExpectation),
  )

  expected_snippets = textwrap.dedent("""\
      migration.testing:bar
          builder_spec = builder_config.builder_spec(
              gclient_config = builder_config.gclient_config(
                  config = "chromium",
              ),
              chromium_config = builder_config.chromium_config(
                  config = "chromium",
              ),
          ),

      migration.testing:bar-tests
          builder_spec = builder_config.builder_spec(
              execution_mode = builder_config.execution_mode.TEST,
              gclient_config = builder_config.gclient_config(
                  config = "chromium",
              ),
              chromium_config = builder_config.chromium_config(
                  config = "chromium",
              ),
          ),

      tryserver.migration.testing:bar
          mirrors = [
              "ci/bar",
          ],
      """)

  yield api.test(
      'migration',
      api.properties(
          migration_operation={
              'builders_to_migrate': [{
                  'builder_group': 'migration.testing',
                  'builder': 'bar',
              }],
              'output_path': '/fake/output/path',
          }),
      api.post_check(lambda check, steps: \
          check(expected_snippets in steps['src-side snippets'].cmd)),
      api.post_process(post_process.DropExpectation),
  )
