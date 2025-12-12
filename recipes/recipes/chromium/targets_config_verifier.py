# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Checks test configs specified in starlark versus testing/buildbot.

Once migration of all specs to starlark is complete, this recipe will be
irrelevant.
"""

from collections.abc import Iterable

from google.protobuf import json_format

from recipe_engine import post_process
from recipe_engine import recipe_test_api

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build import proto_validation

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.chromium import (targets_config_verifier as
                                       targets_config_verifier_pb)

PROPERTIES = targets_config_verifier_pb.InputProperties

DEPS = [
    'chromium_tests',
    'chromium_tests_builder_config',
    'chromium_tests_targets_config_verifier',
    'depot_tools/gclient',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
]


def RunSteps(api, properties: targets_config_verifier_pb.InputProperties):
  api.tryserver.require_is_tryserver()

  errors = VALIDATORS.validate(properties)
  if errors:
    return _result(
        status=common_pb.INFRA_FAILURE,
        elements=errors,
        header='The following errors were found with the input properties:')

  gclient_config = api.gclient.make_config('chromium')

  return api.chromium_tests_targets_config_verifier.verify_target_configs(
      gclient_config,
      properties.builder_config_directory,
      properties.precommit_buckets,
  )


VALIDATORS = proto_validation.Registry()


@VALIDATORS.register(targets_config_verifier_pb.InputProperties)
def _validate_properties(message, ctx):
  ctx.validate_field(message, 'builder_config_directory')


def _result(
    status: common_pb.Status,
    header: str,
    elements: Iterable[str],
) -> result_pb.RawResult:
  summary = [header, '']
  summary.extend('* {}'.format(e) for e in elements)
  return result_pb.RawResult(status=status, summary_markdown='\n'.join(summary))


def GenTests(api):

  builder_config_dir = 'builder-config-dir'
  ctbc_api = api.chromium_tests_builder_config

  def test_data(
      *,
      bucket: str,
      builder: str,
      builder_group: str,
      try_bucket: str | None = None,
      try_builder: str | None = None,
      starlark_targets_spec: dict | None = None,
      testing_buildbot_targets_spec: dict | None = None,
  ) -> recipe_test_api.StepTestData:
    """Set necessary step test data for calling verify_builder_configs.

    Args:
      bucket: The bucket of the builder to verify targets spec for or
        the bucket of the mirrored builder if try_bucket and try_builder
        are set.
      builder: The builder to verify targets spec for or the mirrored
        builder if try_bucket and try_builder are set.
      builder_group: The group of the builder to verify targets spec for
        or the group of the mirrored builder if try_bucket and
        try_builder are set.
      try_bucket: The bucket of the try builder to verify targets spec
        for. Must be set iff try_builder is set.
      try_builder: The try builder to verify targets spec for. Must be
        set iff try_builder is set.
      starlark_targets_spec: The targets spec generated from starlark.
      testing_buildbot_targets_spec: The targets spec generated from
        //testing/buildbot.
    """
    t = api.buildbucket.try_build()
    t += api.properties(
        targets_config_verifier_pb.InputProperties(
            builder_config_directory=builder_config_dir,
            precommit_buckets=[try_bucket] if try_bucket is not None else []))

    def assert_set_together(name1, val1, name2, val2):
      assert (val1 is None) == (val2 is None), (
          f'{name1} and {name2} must both be set or both be unset')

    assert_set_together('try_bucket', try_bucket, 'try_builder', try_builder)

    builder_dir = f'{builder_config_dir}/{try_bucket or bucket}/{try_builder or builder}'
    t += api.tryserver.get_files_affected_by_patch(
        [f'{builder_dir}/targets/{builder_group}.json'],
        step_name=(
            'determine affected targets spec files.git diff to analyze patch'))

    get_targets_config_step = f'get patched targets configs.{builder_dir}'
    verify_step = f'verify {builder_dir}'

    existing_paths = [
        api.path.cache_dir / f'builder/src/{builder_dir}/properties.json'
    ]

    builder_spec = ctbc.BuilderSpec.create(
        gclient_config='chromium',
        chromium_config='chromium',
        chromium_apply_config=['mb'],
    )
    if try_bucket:
      ctbc_prop = ctbc_api.properties_assembler_for_try_builder(
      ).with_mirrored_builder(
          bucket=bucket,
          builder=builder,
          builder_group=builder_group,
          builder_spec=builder_spec,
      )
    else:
      ctbc_prop = ctbc_api.properties_assembler_for_ci_builder(
          bucket=bucket,
          builder=builder,
          builder_group=builder_group,
          builder_spec=builder_spec,
      )

    t += api.step_data(
        f'{verify_step}.read properties file',
        api.file.read_json({
            '$build/chromium_tests_builder_config':
                json_format.MessageToDict(ctbc_prop.assemble()),
        }))

    ctbc_prop = ctbc_prop.with_targets_spec_directory(
        f'{builder_dir}/targets', relative_to_source_dir=True)
    t += api.step_data(
        f'{get_targets_config_step}.read properties file',
        api.file.read_json({
            '$build/chromium_tests_builder_config':
                json_format.MessageToDict(ctbc_prop.assemble()),
        }))

    def read_targets_spec(targets_spec, step_prefix):
      return api.chromium_tests.read_targets_spec(
          builder_group, {builder: targets_spec}, step_prefix=step_prefix)

    if starlark_targets_spec is not None:
      t += read_targets_spec(
          starlark_targets_spec,
          step_prefix=f'{get_targets_config_step}.get starlark targets config.')
    if testing_buildbot_targets_spec is not None:
      existing_paths.append(
          api.path.cache_dir /
          f'builder/src/testing/buildbot/{builder_group}.json')
      t += read_targets_spec(
          testing_buildbot_targets_spec,
          step_prefix=f'{verify_step}.get pyl targets config.')

    t += api.path.exists(*existing_paths)

    return t

  yield api.test(
      'basic',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
          starlark_targets_spec={
              'additional_compile_targets': ['foo'],
              'gtest_tests': [{
                  'test': 'foo-test',
                  'swarming': {},
                  'merge': {
                      'script': '//merge-script',
                  },
              }],
              'scripts': [{
                  'name': 'bar',
                  'script': 'bar.py',
              }],
          },
          testing_buildbot_targets_spec={
              'additional_compile_targets': ['foo'],
              'gtest_tests': [{
                  'test': 'foo-test',
                  'swarming': {},
                  'merge': {
                      'script': '//merge-script',
                  },
              }],
              'scripts': [{
                  'name': 'bar',
                  'script': 'bar.py',
              }],
          },
      ),
      api.post_check(post_process.StepTextContains,
                     'verify builder-config-dir/fake-bucket/fake-builder',
                     ['starlark config matches pyl config']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'mismatch-compile-targets',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
          starlark_targets_spec={
              'additional_compile_targets': ['foo'],
          },
          testing_buildbot_targets_spec={
              'additional_compile_targets': ['bar'],
          },
      ),
      api.expect_status('FAILURE'),
      # Keep just the verify step so that we can see when the diff changes
      api.post_process(
          post_process.Filter(
              'verify builder-config-dir/fake-bucket/fake-builder')),
  )

  yield api.test(
      'mismatch-tests',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
          starlark_targets_spec={
              'gtest_tests': [{
                  'test': 'foo-test',
              }],
          },
          testing_buildbot_targets_spec={
              'gtest_tests': [{
                  'test': 'bar-test',
              }],
          },
      ),
      api.expect_status('FAILURE'),
      # Keep just the verify step so that we can see when the diff changes
      api.post_process(
          post_process.Filter(
              'verify builder-config-dir/fake-bucket/fake-builder')),
  )

  yield api.test(
      'mismatch-tests-non-precommit',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
          starlark_targets_spec={
              'gtest_tests': [{
                  'test': 'foo-test',
              }],
          },
          testing_buildbot_targets_spec={
              'gtest_tests': [{
                  'test': 'foo-test',
                  'non_precommit_args': ['foo'],
              }],
          },
      ),
      api.expect_status('FAILURE'),
      # Keep just the verify step so that we can see when the diff changes
      api.post_process(
          post_process.Filter(
              'verify builder-config-dir/fake-bucket/fake-builder')),
  )

  yield api.test(
      'mismatch-tests-precommit',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
          try_bucket='fake-try-bucket',
          try_builder='fake-try-builder',
          starlark_targets_spec={
              'gtest_tests': [{
                  'test': 'foo-test',
              }],
          },
          testing_buildbot_targets_spec={
              'gtest_tests': [{
                  'test': 'foo-test',
                  'precommit_args': ['foo'],
              }],
          },
      ),
      api.expect_status('FAILURE'),
      # Keep just the verify step so that we can see when the diff changes
      api.post_process(
          post_process.Filter(
              'verify builder-config-dir/fake-try-bucket/fake-try-builder')),
  )

  # Must appear last since it drops expectations
  def invalid_properties(*errors):
    test_data = api.expect_status('INFRA_FAILURE')
    test_data += api.post_check(
        post_process.SummaryMarkdownRE,
        '^The following errors were found with the input properties')
    for error in errors:
      test_data += api.post_check(post_process.SummaryMarkdownRE, error)
    test_data += api.post_process(post_process.DropExpectation)
    return test_data

  yield api.test(
      'builder-config-dir-not-set',
      api.buildbucket.try_build(),
      invalid_properties('builder_config_directory is not set'),
  )
