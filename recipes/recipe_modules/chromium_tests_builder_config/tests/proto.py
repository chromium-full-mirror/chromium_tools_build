# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests_builder_config import proto

from PB.go.chromium.org.luci.buildbucket.proto \
  import builder_common as builder_common_pb
from PB.recipe_modules.build.chromium_tests_builder_config.properties import (
    BuilderSpec, BuilderDatabase, BuilderConfig, InputProperties)

DEPS = [
    'recipe_engine/assertions',
]


def RunSteps(api):

  def assert_invalid(obj, *expected_errors):
    errors = proto.VALIDATORS.validate(obj, '$test')
    for e in expected_errors:
      api.assertions.assertIn(e, errors)

  def assert_valid(obj):
    errors = proto.VALIDATORS.validate(obj, '$test')
    api.assertions.assertFalse(errors)

  # BuilderID
  assert_invalid(
      builder_common_pb.BuilderID(),
      '$test.project is not set',
      '$test.bucket is not set',
      '$test.builder is not set',
  )

  assert_valid(
      builder_common_pb.BuilderID(
          project='project',
          bucket='bucket',
          builder='builder',
      ))

  # LegacyGclientRecipeModuleConfig
  assert_invalid(
      BuilderSpec.LegacyGclientRecipeModuleConfig(),
      '$test.config is not set',
  )

  assert_valid(BuilderSpec.LegacyGclientRecipeModuleConfig(config='config'))

  # LegacyChromiumRecipeModuleConfig
  assert_invalid(
      BuilderSpec.LegacyChromiumRecipeModuleConfig(),
      '$test.config is not set',
  )

  assert_valid(BuilderSpec.LegacyChromiumRecipeModuleConfig(config='config'))

  # LegacyAndroidRecipeModuleConfig
  assert_invalid(
      BuilderSpec.LegacyAndroidRecipeModuleConfig(),
      '$test.config is not set',
  )

  assert_valid(BuilderSpec.LegacyAndroidRecipeModuleConfig(config='config'))

  # SkylabUploadLocation
  assert_invalid(
      BuilderSpec.SkylabUploadLocation(),
      '$test.gs_bucket is not set',
  )

  assert_valid(BuilderSpec.SkylabUploadLocation(gs_bucket='gs_bucket'))

  # ClusterfuzzArchive
  assert_invalid(
      BuilderSpec.ClusterfuzzArchive(),
      '$test.gs_bucket is not set',
      '$test.archive_name_prefix is not set',
  )

  assert_valid(
      BuilderSpec.ClusterfuzzArchive(
          gs_bucket='gs_bucket', archive_name_prefix='archive_name_prefix'))

  # BuilderSpec
  assert_invalid(
      BuilderSpec(),
      '$test.builder_group is not set',
      '$test.execution_mode is not set',
      '$test.legacy_gclient_config is not set',
      '$test.legacy_chromium_config is not set',
  )

  assert_invalid(
      BuilderSpec(
          legacy_gclient_config=BuilderSpec.LegacyGclientRecipeModuleConfig(),
          legacy_chromium_config=BuilderSpec.LegacyChromiumRecipeModuleConfig(),
          legacy_android_config=BuilderSpec.LegacyAndroidRecipeModuleConfig(),
          skylab_upload_location=BuilderSpec.SkylabUploadLocation(),
      ),
      '$test.legacy_gclient_config.config is not set',
      '$test.legacy_chromium_config.config is not set',
      '$test.legacy_android_config.config is not set',
      '$test.skylab_upload_location.gs_bucket is not set',
  )

  minimal_valid_build_spec = BuilderSpec(
      builder_group='fake-group',
      execution_mode=BuilderSpec.ExecutionMode.COMPILE_AND_TEST,
      legacy_gclient_config=BuilderSpec.LegacyGclientRecipeModuleConfig(
          config='config'),
      legacy_chromium_config=BuilderSpec.LegacyChromiumRecipeModuleConfig(
          config='config'),
  )

  assert_valid(minimal_valid_build_spec)

  # BuilderDatabase.Entry
  assert_invalid(BuilderDatabase.Entry(), '$test.builder_id is not set',
                 '$test.builder_spec is not set')

  assert_invalid(
      BuilderDatabase.Entry(
          builder_id=builder_common_pb.BuilderID(),
          builder_spec=BuilderSpec(),
      ),
      '$test.builder_id.builder is not set',
      '$test.builder_spec.builder_group is not set',
  )

  # BuilderDatabase
  assert_invalid(
      BuilderDatabase(),
      '$test.entries is empty',
  )

  assert_invalid(
      BuilderDatabase(entries=[
          BuilderDatabase.Entry(builder_id=builder_common_pb.BuilderID()),
          BuilderDatabase.Entry(builder_spec=BuilderSpec()),
          BuilderDatabase.Entry(
              builder_id=builder_common_pb.BuilderID(
                  project='project',
                  bucket='bucket',
                  builder='builder',
              ),
              builder_spec=minimal_valid_build_spec,
          ),
          BuilderDatabase.Entry(
              builder_id=builder_common_pb.BuilderID(
                  project='project',
                  bucket='bucket',
                  builder='builder',
              ),
              builder_spec=minimal_valid_build_spec,
          ),
      ]),
      '$test.entries[0].builder_spec is not set',
      '$test.entries[1].builder_id is not set',
      '$test.entries[3].builder_id is the same as $test.entries[2].builder_id',
  )

  assert_valid(
      BuilderDatabase(entries=[
          BuilderDatabase.Entry(
              builder_id=builder_common_pb.BuilderID(
                  project='project',
                  bucket='bucket',
                  builder='builder',
              ),
              builder_spec=minimal_valid_build_spec,
          ),
          BuilderDatabase.Entry(
              builder_id=builder_common_pb.BuilderID(
                  project='project2',
                  bucket='bucket',
                  builder='builder',
              ),
              builder_spec=minimal_valid_build_spec,
          ),
          BuilderDatabase.Entry(
              builder_id=builder_common_pb.BuilderID(
                  project='project',
                  bucket='bucket2',
                  builder='builder',
              ),
              builder_spec=minimal_valid_build_spec,
          ),
          BuilderDatabase.Entry(
              builder_id=builder_common_pb.BuilderID(
                  project='project',
                  bucket='bucket',
                  builder='builder2',
              ),
              builder_spec=minimal_valid_build_spec,
          ),
      ]))

  # BuilderGroupAndName
  assert_invalid(
      BuilderConfig.BuilderGroupAndName(),
      '$test.group is not set',
      '$test.builder is not set',
  )

  assert_valid(
      BuilderConfig.BuilderGroupAndName(
          group='group',
          builder='builder',
      ))

  # BuilderConfig
  assert_invalid(
      BuilderConfig(),
      '$test.builder_db is not set',
      '$test.builder_ids is empty',
  )

  assert_invalid(
      BuilderConfig(
          builder_db=BuilderDatabase(),
          builder_ids=[
              builder_common_pb.BuilderID(
                  project='project',
                  bucket='bucket',
              ),
              builder_common_pb.BuilderID(
                  project='project',
                  bucket='bucket',
                  builder='builder',
              ),
          ],
          builder_ids_in_scope_for_testing=[
              builder_common_pb.BuilderID(
                  project='project',
                  bucket='bucket',
              ),
              builder_common_pb.BuilderID(
                  project='project',
                  bucket='bucket',
                  builder='builder',
              ),
          ],
          mirroring_builder_group_and_names=[
              BuilderConfig.BuilderGroupAndName(group='group'),
          ],
      ),
      '$test.builder_db.entries is empty',
      '$test.builder_ids[0].builder is not set',
      'there is no entry in $test.builder_db for $test.builder_ids[1]',
      '$test.builder_ids_in_scope_for_testing[0].builder is not set',
      ('there is no entry in $test.builder_db for'
       ' $test.builder_ids_in_scope_for_testing[1]'),
      '$test.mirroring_builder_group_and_names[0].builder is not set',
  )

  assert_valid(
      BuilderConfig(
          builder_db=BuilderDatabase(entries=[
              BuilderDatabase.Entry(
                  builder_id=builder_common_pb.BuilderID(
                      project='project',
                      bucket='bucket',
                      builder='builder',
                  ),
                  builder_spec=minimal_valid_build_spec,
              )
          ]),
          builder_ids=[
              builder_common_pb.BuilderID(
                  project='project',
                  bucket='bucket',
                  builder='builder',
              )
          ],
      ))

  # InputProperties
  assert_invalid(
      InputProperties(builder_config=BuilderConfig()),
      '$test.builder_config.builder_db is not set',
  )

  assert_invalid(InputProperties())


  # Test conversion with no_history and shallow
  proto_db_shallow = BuilderDatabase(entries=[
      BuilderDatabase.Entry(
          builder_id=builder_common_pb.BuilderID(
              project='project',
              bucket='bucket',
              builder='builder',
          ),
          builder_spec=BuilderSpec(
              builder_group='fake-group',
              execution_mode=BuilderSpec.ExecutionMode.COMPILE_AND_TEST,
              legacy_gclient_config=BuilderSpec.LegacyGclientRecipeModuleConfig(
                  config='config'),
              legacy_chromium_config=BuilderSpec
              .LegacyChromiumRecipeModuleConfig(config='config'),
              no_history=True,
              shallow=True,
          ))
  ])

  proto_config_shallow = BuilderConfig(
      builder_db=proto_db_shallow,
      builder_ids=[
          builder_common_pb.BuilderID(
              project='project',
              bucket='bucket',
              builder='builder',
          )
      ])

  converted_config_shallow = proto.convert_builder_config(proto_config_shallow)
  assert_valid(InputProperties(builder_config=proto_config_shallow))
  api.assertions.assertTrue(converted_config_shallow.no_history)
  api.assertions.assertTrue(converted_config_shallow.shallow)


def GenTests(api):
  yield api.test(
      'full',
      api.post_process(post_process.DropExpectation),
  )
