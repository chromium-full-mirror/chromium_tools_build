# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test of the chromium_tests_targets_config_verifier module."""

from google.protobuf import json_format

from recipe_engine import post_process
from recipe_engine import recipe_test_api

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

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


def RunSteps(api):
  gclient_config = api.gclient.make_config('chromium')
  if (apply_gclient_config := api.properties.get('gclient_apply_config', None)):
    api.gclient.apply_config(apply_gclient_config, gclient_config)
  return api.chromium_tests_targets_config_verifier.verify_target_configs(
      gclient_config, 'fake-builder-config-dir', ['fake-try-bucket'])


_CTBC_PROPERTY = '$build/chromium_tests_builder_config'


def GenTests(api):

  ctbc_api = api.chromium_tests_builder_config

  def test_data(
      *,
      bucket: str,
      builder: str,
      builder_group: str,
      non_existent_tester: str | None = None,
      non_existent_tester_group: str | None = None,
      try_bucket: str | None = None,
      try_builder: str | None = None,
      builder_spec: ctbc.BuilderSpec | None = None,
      with_properties_file: bool = True,
      with_ctbc_property: bool = True,
      with_targets_spec_directory: bool = True,
      with_bootstrapped_builder_config_without_patch: bool = True,
      with_targets_spec_directory_without_patch: bool = False,
      starlark_targets_spec: dict | None = None,
      testing_buildbot_targets_spec: dict | None = None,
      patch_dep: bool = False,
      testing_buildbot_targets_specs_in_patch_repo: bool = True,
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
      non_existent_tester: The name of a tester that there is no targets
        spec for without patch. Must be set iff
        non_existent_tester_group is set.
      non_existent_tester_group: The builder group of the non-existent
        tester. Must be set iff non_existent_tester is set. Must not be
        equal to builder_group.
      try_bucket: The bucket of the try builder to verify targets spec
        for. Must be set iff try_builder is set. Cannot be set if
        non_existent_tester is set.
      try_builder: The try builder to verify targets spec for. Must be
        set iff try_builder is set.
      with_ctbc_property - Whether or not the
        $build/chromium_tests_builder_config_property should be set for
        the builder.
      with_targets_spec_directory: Whether or not the
        targets_spec_directory field should be set in the
        $build/chromium_tests_builder_config property.
      starlark_targets_spec: The targets spec generated from starlark
      testing_buildbot_targets_spec: The targets spec generated from
        //testing/buildbot. Cannot be set if non_existent_tester is set.
      patch_dep: Whether the test data should be simulating a patch to a
        dependency repo instead of the top-level repo.
      testing_buildbot_targets_specs_in_patch_repo: Whether the targets
        specs generated from //testing/buildbot are in the repo that the
        patch is for. If False, the specs will be present in the
        top-level repo.
    """
    def assert_set_together(name1, val1, name2, val2):
      assert (val1 is None) == (val2 is None), (
          f'{name1} and {name2} must both be set or both be unset')

    assert_set_together('try_bucket', try_bucket, 'try_builder', try_builder)

    assert_set_together('non_existent_tester', non_existent_tester,
                        'non_existent_tester_group', non_existent_tester_group)
    if non_existent_tester is not None:
      assert non_existent_tester_group is not None, (
          'non_existent_tester_group must be set if tester is set')
      assert non_existent_tester_group != builder_group, (
          'non_existent_tester_group must not be equal to builder_group')
      assert try_bucket is None, (
          "non_existent_tester and try_bucket can't both be set")
      assert testing_buildbot_targets_spec is None, (
          'non_existent_tester and testing_buildbot_targets_spec'
          " can't both be set")

    t = api.empty_test_data()

    if patch_dep:
      git_repo = 'https://chrome-internal.googlesource.com/chrome/src-internal'
      repo_rel_path = 'internal/'
      t += api.properties(gclient_apply_config='checkout_src_internal_infra')
    else:
      git_repo = 'https://chromium.googlesource.com/chromium/src'
      repo_rel_path = ''

    t += api.buildbucket.try_build(git_repo=git_repo)

    builder_dir = f'fake-builder-config-dir/{try_bucket or bucket}/{try_builder or builder}'
    t += api.tryserver.get_files_affected_by_patch(
        [f'{builder_dir}/targets/{builder_group}.json'],
        step_name=(
            'determine affected targets spec files.git diff to analyze patch'))

    get_targets_config_step = f'get patched targets configs.{builder_dir}'
    verify_step = f'verify {builder_dir}'

    existing_paths = []

    if with_properties_file:
      existing_paths.append(
          api.path.cache_dir /
          f'builder/src/{repo_rel_path}{builder_dir}/properties.json')
      if with_ctbc_property:
        if not builder_spec:
          chromium_apply_config = ['mb']
          if patch_dep and testing_buildbot_targets_specs_in_patch_repo:
            chromium_apply_config.append('internal_targets_specs')
          builder_spec = ctbc.BuilderSpec.create(
              gclient_config='chromium',
              chromium_config='chromium',
              chromium_apply_config=chromium_apply_config,
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
          if non_existent_tester_group:
            ctbc_prop.with_tester(
                bucket=bucket,
                builder=non_existent_tester,
                builder_group=non_existent_tester_group,
            )
        if with_targets_spec_directory:
          if with_bootstrapped_builder_config_without_patch:
            if with_targets_spec_directory_without_patch:
              ctbc_prop = ctbc_prop.with_targets_spec_directory(
                  f'{builder_dir}/targets')
            t += api.step_data(
                f'{verify_step}.read properties file',
                api.file.read_json({
                    '$build/chromium_tests_builder_config':
                        json_format.MessageToDict(ctbc_prop.assemble()),
                }))
          ctbc_prop = ctbc_prop.with_targets_spec_directory(
              f'{builder_dir}/targets')
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
      if testing_buildbot_targets_specs_in_patch_repo:
        rel_path = repo_rel_path
      else:
        rel_path = ''
      existing_paths.append(
          api.path.cache_dir /
          f'builder/src/{rel_path}testing/buildbot/{builder_group}.json')
      t += read_targets_spec(
          testing_buildbot_targets_spec,
          step_prefix=f'{verify_step}.get pyl targets config.')
    # In the case where there isn't the targets spec file for the tester, we
    # need to indicate that the targets spec file for the builder exists
    if non_existent_tester:
      existing_paths.append(
          api.path.cache_dir /
          f'builder/src/{repo_rel_path}testing/buildbot/{builder_group}.json')

    if existing_paths:
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
                     'verify fake-builder-config-dir/fake-bucket/fake-builder',
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
              'verify fake-builder-config-dir/fake-bucket/fake-builder')),
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
              'verify fake-builder-config-dir/fake-bucket/fake-builder')),
  )

  yield api.test(
      'mismatch-tests-wrapping',
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
                  'experiment_percentage': '50',
                  'ci_only': True,
              }],
          },
      ),
      api.expect_status('FAILURE'),
      # Keep just the verify step so that we can see when the diff changes
      api.post_process(
          post_process.Filter(
              'verify fake-builder-config-dir/fake-bucket/fake-builder')),
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
              'verify fake-builder-config-dir/fake-bucket/fake-builder')),
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
              'verify fake-builder-config-dir/fake-try-bucket/fake-try-builder')
      ),
  )

  yield api.test(
      'not-bootstrapped',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
          with_properties_file=False,
      ),
      api.post_check(
          post_process.StepTextContains,
          ('get patched targets configs.'
           'fake-builder-config-dir/fake-bucket/fake-builder'),
          ["builder doesn't have bootstrapped builder config, can't verify"]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no-ctbc-property',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
          with_ctbc_property=False,
      ),
      api.post_check(
          post_process.StepTextContains,
          ('get patched targets configs.'
           'fake-builder-config-dir/fake-bucket/fake-builder'),
          ["builder doesn't have bootstrapped builder config, can't verify"]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no-targets-spec-directory',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
          with_targets_spec_directory=False,
      ),
      api.post_check(post_process.StepTextContains,
                     ('get patched targets configs.'
                      'fake-builder-config-dir/fake-bucket/fake-builder'),
                     [(f'targets_spec_directory is not set in {_CTBC_PROPERTY},'
                       ' nothing to verify')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no-bootstrapped-builder-config-without-patch',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
          with_bootstrapped_builder_config_without_patch=False,
      ),
      api.post_check(
          post_process.StepTextContains,
          'verify fake-builder-config-dir/fake-bucket/fake-builder',
          [("builder didn't have bootstrapped builder config without patch,"
            " can't verify")]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'already-using-starlark-tests',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
          with_targets_spec_directory_without_patch=True,
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no-tests-specified-previously',
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
          testing_buildbot_targets_spec={},
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no-targets-spec-files',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'some-targets-spec-files',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-builder-group',
          non_existent_tester='fake-tester',
          non_existent_tester_group='fake-tester-group',
      ),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  builder_dir = 'fake-builder-config-dir/fake-bucket/fake-builder'
  yield api.test(
      'deleted-builder',
      api.buildbucket.try_build(),
      api.tryserver.get_files_affected_by_patch(
          [
              f'{builder_dir}/properties.json',
              f'{builder_dir}/targets/fake-builder-group.json',
          ],
          step_name=(
              'determine affected targets spec files.git diff to analyze patch'
          ),
      ),
      api.post_check(post_process.MustRun,
                     'all affected builders are being deleted'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'patch-dir-is-not-source-dir',
      test_data(
          patch_dep=True,
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
                     'verify fake-builder-config-dir/fake-bucket/fake-builder',
                     ['starlark config matches pyl config']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'patch-dir-is-not-source-dir-without-patch-specs-in-source-dir',
      test_data(
          patch_dep=True,
          testing_buildbot_targets_specs_in_patch_repo=False,
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
                     'verify fake-builder-config-dir/fake-bucket/fake-builder',
                     ['starlark config matches pyl config']),
      api.post_process(post_process.DropExpectation),
  )
