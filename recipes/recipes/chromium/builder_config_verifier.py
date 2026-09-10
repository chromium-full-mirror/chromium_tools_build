# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Checks src-side builder configs against the recipe config objects.

Once migration of all specs to src is complete, this recipe will be
irrelevant.
"""

import attr

from google.protobuf import json_format

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build import proto_validation

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.chromium import (
  builder_config_verifier as builder_config_verifier_pb,
)

PROPERTIES = builder_config_verifier_pb.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium_tests_builder_config,
  chromium_tests_builder_config_verifier,
)
from RECIPE_MODULES.depot_tools import bot_update, gclient, tryserver
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  file,
  json,
  path,
  properties,
  raw_io,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  chromium_tests_builder_config_verifier: (
    chromium_tests_builder_config_verifier.API
  )
  context: context.API
  file: file.API
  gclient: gclient.API
  json: json.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  chromium_tests_builder_config_verifier: (
    chromium_tests_builder_config_verifier.TEST_API
  )
  json: json.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS, properties):
  api.tryserver.require_is_tryserver()

  errors = VALIDATORS.validate(properties)
  if errors:
    return _result(
      status=common_pb.INFRA_FAILURE,
      elements=errors,
      header='The following errors were found with the input properties:',
    )

  gclient_config = api.gclient.make_config()
  s = gclient_config.solutions.add()
  s.url = api.tryserver.gerrit_change_repo_url
  s.name = s.url.rsplit('/', 1)[-1]
  gclient_config.got_revision_mapping[s.name] = 'got_revision'

  with api.context(cwd=api.path.cache_dir / 'builder'):
    update_result = api.bot_update.ensure_checkout(
      patch=True, gclient_config=gclient_config
    )

  repo_path = update_result.source_root.path

  ctbc_api = api.chromium_tests_builder_config
  return api.chromium_tests_builder_config_verifier.verify_builder_configs(
    repo_path,
    properties.builder_config_directory,
    [(ctbc_api.builder_db, ctbc_api.try_db)],
    ['try'],
  )


VALIDATORS = proto_validation.Registry()


@VALIDATORS.register(builder_config_verifier_pb.InputProperties)
def _validate_properties(message, ctx):
  ctx.validate_field(message, 'builder_config_directory')


def _result(status, header, elements):
  summary = [header, '']
  summary.extend('* {}'.format(e) for e in elements)
  return result_pb.RawResult(status=status, summary_markdown='\n'.join(summary))


def GenTests(api: TEST_DEPS):

  def properties(**kwargs):
    return api.properties(builder_config_verifier_pb.InputProperties(**kwargs))

  def dumps(obj):
    return api.json.dumps(obj, indent=2)

  example_spec = ctbc.BuilderSpec.create(
    chromium_config='chromium',
    gclient_config='chromium',
  )

  ctbc_api = api.chromium_tests_builder_config
  ctbcv_api = api.chromium_tests_builder_config_verifier

  ctbc_prop = (
    ctbc_api.properties_assembler_for_ci_builder(
      bucket='bucket',
      builder='matching-config',
      builder_group='fake-group',
      builder_spec=example_spec,
    )
    .with_tester(
      builder='matching-config-tester',
      builder_group='fake-group',
    )
    .assemble()
  )

  yield api.test(
    'changed-builder-config-matching-recipe-config',
    api.buildbucket.try_build(),
    properties(builder_config_directory='props-files'),
    ctbcv_api.test_case(
      properties_files_directory='props-files',
      properties_files={
        'props-files/bucket/matching-config/properties.json': ctbcv_api.Contents(
          patched=dumps(
            {
              '$build/chromium_tests_builder_config': json_format.MessageToDict(
                ctbc_prop
              ),
              'builder_group': 'fake-group',
            }
          )
        ),
      },
    ),
    api.chromium_tests_builder_config.databases(
      ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'matching-config': example_spec,
            'matching-config-tester': attr.evolve(
              example_spec,
              execution_mode=ctbc.TEST,
              parent_buildername='matching-config',
            ),
          },
        }
      )
    ),
    api.post_process(post_process.DropExpectation),
  )

  ctbc_prop = ctbc_api.properties_assembler_for_ci_builder(
    bucket='bucket',
    builder='not-matching-config',
    builder_group='fake-group',
    builder_spec=example_spec,
  ).assemble()

  yield api.test(
    'changed-builder-config-not-matching-recipe-config',
    api.buildbucket.try_build(),
    properties(builder_config_directory='props-files'),
    ctbcv_api.test_case(
      properties_files_directory='props-files',
      properties_files={
        'props-files/bucket/not-matching-config/properties.json': ctbcv_api.Contents(
          patched=dumps(
            {
              '$build/chromium_tests_builder_config': json_format.MessageToDict(
                ctbc_prop
              ),
              'builder_group': 'fake-group',
            }
          )
        ),
      },
    ),
    api.chromium_tests_builder_config.databases(
      ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'not-matching-config': ctbc.BuilderSpec.create(
              chromium_config='not-chromium',
              chromium_config_kwargs={
                'TARGET_PLATFORM': 'mac',
                'TARGET_BITS': 32,
              },
              gclient_config='not-chromium',
              gclient_apply_config=['foo', 'bar'],
            ),
          }
        }
      )
    ),
    api.expect_status('FAILURE'),
    # Keep just the verify step so that we can see when the diff changes
    api.post_process(
      post_process.Filter(
        'verify props-files/bucket/not-matching-config/properties.json'
      )
    ),
  )

  # Must appear last since it drops expectations
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
    'builder-config-dir-not-set',
    api.buildbucket.try_build(),
    invalid_properties('builder_config_directory is not set'),
  )
