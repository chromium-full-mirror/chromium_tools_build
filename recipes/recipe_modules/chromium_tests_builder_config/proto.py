# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Utilities for handling the chromium_tests_builder_config properties.

This module contains two functions: validate and convert.

Validate takes a proto message object and runs any registered validator
function for the proto message. A list of strings containing validation
errors will be returned. There are validators registered for
InputProperties and all messages that can appear within InputProperties.

Convert takes a proto message object and kwargs containing any
additional information necessary for conversion and runs the registered
converted function for the proto message. An object representing the
same conceptual value will be returned. Converters are registered for
the following types with the specified additional keyword arguments:
* BuilderConfig
* BuilderDatabase
  * builder_id_by_bb_builder_id - A mapping from BuildbucketBuilderID to
    chromium_types.BuilderId. This allows for converting the proto message
    BuilderID to the in-memory BuilderId, which uses the builder group
    instead of the project and bucket.
* BuilderSpec
  * builder_id_by_bb_builder_id - A mapping from BuildbucketBuilderID to
    chromium_types.BuilderId. This allows for converting the proto message
    BuilderID to the in-memory BuilderId, which uses the builder group
    instead of the project and bucket.
"""

from __future__ import annotations

import collections

from RECIPE_MODULES.build.chromium_types import BuilderId
from RECIPE_MODULES.build import proto_validation

from PB.go.chromium.org.luci.buildbucket.proto \
  import builder_common as builder_common_pb
from PB.recipe_modules.build.chromium_tests_builder_config import (properties as
                                                                   properties_pb
                                                                  )

from .builder_config import BuildbucketBuilderId, BuilderConfig
from .builder_db import BuilderDatabase
from .builder_spec import BuilderSpec, COMPILE_AND_TEST, TEST
from .try_spec import ALWAYS, NEVER

VALIDATORS = proto_validation.Registry()


@VALIDATORS.register(builder_common_pb.BuilderID)
def _validate_builder_id(obj, ctx):
  ctx.validate_field(obj, 'project')
  ctx.validate_field(obj, 'bucket')
  ctx.validate_field(obj, 'builder')


@VALIDATORS.register(properties_pb.BuilderSpec.LegacyGclientRecipeModuleConfig)
def _validate_legacy_gclient_recipe_module_config(obj, ctx):
  ctx.validate_field(obj, 'config')


@VALIDATORS.register(properties_pb.BuilderSpec.LegacyChromiumRecipeModuleConfig)
def _validate_legacy_chromium_recipe_module_config(obj, ctx):
  ctx.validate_field(obj, 'config')


@VALIDATORS.register(properties_pb.BuilderSpec.LegacyAndroidRecipeModuleConfig)
def _validate_legacy_android_recipe_module_config(obj, ctx):
  ctx.validate_field(obj, 'config')


@VALIDATORS.register(properties_pb.BuilderSpec.SkylabUploadLocation)
def _validate_skylab_upload_location(obj, ctx):
  ctx.validate_field(obj, 'gs_bucket')


@VALIDATORS.register(properties_pb.BuilderSpec.ClusterfuzzArchive)
def _validate_clusterfuzz_archive(obj, ctx):
  ctx.validate_field(obj, 'gs_bucket')
  ctx.validate_field(obj, 'archive_name_prefix')


@VALIDATORS.register(properties_pb.BuilderSpec)
def _validate_builder_spec(obj, ctx):
  ctx.validate_field(obj, 'builder_group')
  ctx.validate_field(obj, 'execution_mode')
  ctx.validate_field(obj, 'legacy_gclient_config')
  ctx.validate_field(obj, 'legacy_chromium_config')
  ctx.validate_field(obj, 'legacy_android_config', optional=True)
  ctx.validate_field(obj, 'skylab_upload_location', optional=True)
  ctx.validate_field(obj, 'clusterfuzz_archive', optional=True)


_EXECUTION_MODE_MAP = {
    properties_pb.BuilderSpec.ExecutionMode.COMPILE_AND_TEST: COMPILE_AND_TEST,
    properties_pb.BuilderSpec.ExecutionMode.TEST: TEST,
}


def _convert_builder_spec(obj, builder_id_by_bb_builder_id):
  parent_id = builder_id_by_bb_builder_id.get(
      BuildbucketBuilderId.from_proto(obj.parent))

  legacy_chromium_config = obj.legacy_chromium_config
  chromium_config_kwargs = {}
  for a in (
      'build_config',
      'host_platform',
      'target_arch',
      'target_bits',
      'target_platform',
  ):
    if legacy_chromium_config.HasField(a):
      chromium_config_kwargs[a.upper()] = getattr(legacy_chromium_config, a)
  for a in (
      'target_cros_boards',
      'cros_boards_with_qemu_images',
  ):
    val = getattr(legacy_chromium_config, a)
    if val:
      chromium_config_kwargs[a.upper()] = ':'.join(val)

  kwargs = {}
  if obj.HasField('clusterfuzz_archive'):
    kwargs.update(
        dict(
            cf_archive_build=True,
            cf_gs_bucket=obj.clusterfuzz_archive.gs_bucket or None,
            cf_gs_acl=obj.clusterfuzz_archive.gs_acl or None,
            cf_archive_name=obj.clusterfuzz_archive.archive_name_prefix or None,
            cf_archive_subdir_suffix=obj.clusterfuzz_archive.archive_subdir or
            None,
            cf_archive_schema_version=obj.clusterfuzz_archive
            .archive_schema_version,
        ))

  return BuilderSpec.create(
      execution_mode=_EXECUTION_MODE_MAP[obj.execution_mode],
      parent_builder_group=parent_id.group if parent_id else None,
      parent_buildername=parent_id.builder if parent_id else None,
      gclient_config=obj.legacy_gclient_config.config,
      gclient_apply_config=obj.legacy_gclient_config.apply_configs,
      chromium_config=legacy_chromium_config.config,
      chromium_apply_config=legacy_chromium_config.apply_configs,
      chromium_config_kwargs=chromium_config_kwargs,
      android_config=obj.legacy_android_config.config or None,
      android_apply_config=obj.legacy_android_config.apply_configs,
      android_version=obj.android_version_file or None,
      clobber=obj.clobber,
      mb_phase_for_tests=obj.mb_phase_for_tests or None,
      serialize_tests=obj.run_tests_serially,
      perf_isolate_upload=obj.perf_isolate_upload,
      expose_trigger_properties=obj.expose_trigger_properties,
      skylab_gs_bucket=obj.skylab_upload_location.gs_bucket or None,
      skylab_gs_extra=obj.skylab_upload_location.gs_extra or None,
      **kwargs)


@VALIDATORS.register(properties_pb.BuilderDatabase.Entry)
def _validate_builder_database_entry(obj, ctx):
  ctx.validate_field(obj, 'builder_id')
  ctx.validate_field(obj, 'builder_spec')


@VALIDATORS.register(properties_pb.BuilderDatabase)
def _validate_builder_database(obj, ctx):
  location_by_builder_key = {}

  def check_builder_id_unique(entry, sub_ctx):
    builder_key = BuildbucketBuilderId.from_proto(entry.builder_id)
    if builder_key in location_by_builder_key:
      sub_ctx.error('{}.builder_id is the same as {}.builder_id'.format(
          sub_ctx.location, location_by_builder_key[builder_key]))
    else:
      location_by_builder_key[builder_key] = sub_ctx.location

  ctx.validate_repeated_field(obj, 'entries', callback=check_builder_id_unique)


def _convert_builder_database(obj, builder_id_by_bb_builder_id):
  builders = collections.defaultdict(dict)
  for entry in obj.entries:
    bb_builder_id = BuildbucketBuilderId.from_proto(entry.builder_id)
    builder_id = builder_id_by_bb_builder_id[bb_builder_id]
    builders[builder_id.group][builder_id.builder] = _convert_builder_spec(
        entry.builder_spec,
        builder_id_by_bb_builder_id=builder_id_by_bb_builder_id,
    )
  return BuilderDatabase.create(builders)


@VALIDATORS.register(properties_pb.BuilderConfig.BuilderGroupAndName)
def _validate_builder_group_and_name(obj, ctx):
  ctx.validate_field(obj, 'group')
  ctx.validate_field(obj, 'builder')


@VALIDATORS.register(properties_pb.BuilderConfig)
def _validate_builder_config(obj, ctx):
  builders = set(
      BuildbucketBuilderId.from_proto(e.builder_id)
      for e in obj.builder_db.entries)

  def check_builder_id_in_db(builder_id, sub_ctx):
    if BuildbucketBuilderId.from_proto(builder_id) not in builders:
      sub_ctx.error('there is no entry in {}.builder_db for {}'.format(
          ctx.location, sub_ctx.location))

  ctx.validate_field(obj, 'builder_db')
  ctx.validate_repeated_field(
      obj, 'builder_ids', callback=check_builder_id_in_db)
  ctx.validate_repeated_field(
      obj,
      'builder_ids_in_scope_for_testing',
      optional=True,
      callback=check_builder_id_in_db)
  ctx.validate_repeated_field(
      obj, 'mirroring_builder_group_and_names', optional=True)


_RTS_CONDITION_MAP = {
    properties_pb.BuilderConfig.RtsConfig.Condition.NEVER: NEVER,
    properties_pb.BuilderConfig.RtsConfig.Condition.ALWAYS: ALWAYS,
}


def convert_builder_config(obj, default_retry_failed_shards=True):
  # The builder ID in the protos is (project, bucket, builder), whereas the
  # one in the recipes is (group, builder), so we need to map between them
  # until such time as the recipes version uses project and bucket
  builder_id_by_bb_builder_id = {
      BuildbucketBuilderId.from_proto(entry.builder_id):
      BuilderId.create_for_group(entry.builder_spec.builder_group,
                                 entry.builder_id.builder)
      for entry in obj.builder_db.entries
  }

  rts_condition = obj.rts_config and obj.rts_config.condition
  # coerce an unspecified condition to None
  rts_condition = rts_condition or None
  regression_test_selection = (
      _RTS_CONDITION_MAP[rts_condition] if rts_condition is not None else None)

  return BuilderConfig.create(
      builder_db=_convert_builder_database(
          obj.builder_db,
          builder_id_by_bb_builder_id=builder_id_by_bb_builder_id),
      builder_ids=[
          builder_id_by_bb_builder_id[BuildbucketBuilderId.from_proto(b)]
          for b in obj.builder_ids
      ],
      builder_ids_in_scope_for_testing=[
          builder_id_by_bb_builder_id[BuildbucketBuilderId.from_proto(b)]
          for b in obj.builder_ids_in_scope_for_testing
      ],
      bb_builder_id_by_builder_id={
          v: k for k, v in builder_id_by_bb_builder_id.items()
      },
      mirroring_try_builders=[
          BuilderId.create_for_group(x.group, x.builder)
          for x in obj.mirroring_builder_group_and_names
      ],
      targets_spec_directory=obj.targets_spec_directory or None,
      include_all_triggered_testers=False,
      is_compile_only=obj.is_compile_only,
      analyze_names=obj.analyze_names,
      additional_exclusions=obj.additional_exclusions or None,
      retry_failed_shards=(obj.retry_failed_shards
                           if obj.HasField('retry_failed_shards') else
                           default_retry_failed_shards),
      retry_invalid_shards=(obj.retry_invalid_shards
                            if obj.HasField('retry_invalid_shards') else False),
      retry_without_patch=(obj.retry_without_patch
                           if obj.HasField('retry_without_patch') else True),
      regression_test_selection=regression_test_selection,
  )


@VALIDATORS.register(properties_pb.InputProperties)
def _validate_input_properties(obj, ctx):
  ctx.validate_field(obj, 'builder_config', optional=True)
