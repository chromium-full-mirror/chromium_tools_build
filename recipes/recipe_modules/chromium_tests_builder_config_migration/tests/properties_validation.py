# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from PB.recipe_modules.build.chromium_tests_builder_config_migration import (
  properties as properties_pb,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium_tests_builder_config,
  chromium_tests_builder_config_migration,
)
from RECIPE_MODULES.recipe_engine import properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium_tests_builder_config: chromium_tests_builder_config.API
  chromium_tests_builder_config_migration: (
    chromium_tests_builder_config_migration.API
  )
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API


PROPERTIES = properties_pb.InputProperties


def RunSteps(api: DEPS, properties):
  ctbc_api = api.chromium_tests_builder_config
  return api.chromium_tests_builder_config_migration(
    properties, ctbc_api.builder_db, ctbc_api.try_db
  )


def GenTests(api: TEST_DEPS):

  def invalid_properties(*errors):
    test_data = api.expect_status('INFRA_FAILURE')
    test_data += api.post_check(
      post_process.SummaryMarkdownRE,
      '^The following errors were found with the input properties',
    )
    for error in errors:
      test_data += api.post_check(post_process.SummaryMarkdownRE, error)
    test_data += api.post_process(post_process.DropExpectation)
    return test_data

  yield api.test(
    'no-operation',
    invalid_properties('no operation is set'),
  )

  yield api.test(
    'bad-groupings-operation',
    api.properties(groupings_operation={}),
    invalid_properties(
      r'groupings_operation\.output_path is not set',
      r'groupings_operation\.builder_group_filters is empty',
    ),
  )

  yield api.test(
    'bad-builder-group-filter',
    api.properties(
      groupings_operation={
        'builder_group_filters': [{}],
      }
    ),
    invalid_properties(
      (
        r'groupings_operation\.builder_group_filters\[0\]'
        r'\.builder_group_regex is not set'
      )
    ),
  )

  yield api.test(
    'bad-migration-operation',
    api.properties(migration_operation={}),
    invalid_properties(
      r'migration_operation\.builders_to_migrate is empty',
      r'migration_operation\.output_path is not set',
    ),
  )

  yield api.test(
    'bad-builder-to-migrate',
    api.properties(
      migration_operation={
        'builders_to_migrate': [{}],
      }
    ),
    invalid_properties(
      (
        r'migration_operation\.builders_to_migrate\[0\]'
        r'\.builder_group is not set'
      ),
      r'migration_operation\.builders_to_migrate\[0\]\.builder is not set',
    ),
  )
