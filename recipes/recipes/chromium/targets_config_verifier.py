# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Checks test configs specified in starlark versus testing/buildbot.

Once migration of all specs to starlark is complete, this recipe will be
irrelevant.
"""

from collections.abc import Iterable, Sequence
import difflib
import re

import attr
from google.protobuf import json_format

from recipe_engine import post_process
from recipe_engine import recipe_test_api
from recipe_engine.config_types import Path
from recipe_engine.engine_types import FrozenDict, thaw

from RECIPE_MODULES.build import chromium_tests
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build import proto_validation
from RECIPE_MODULES.build.chromium_tests import (generators, targets_config as
                                                 targets_config_module)

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.chromium import (targets_config_verifier as
                                       targets_config_verifier_pb)
from PB.recipe_modules.build.chromium_tests_builder_config import (
    properties as ctbc_properties_pb)

PROPERTIES = targets_config_verifier_pb.InputProperties

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]

_CTBC_PROPERTY = '$build/chromium_tests_builder_config'


def _targets_spec_file_re(builder_config_directory: str) -> re.Pattern:
  """Get a regular expression that will match a targets spec file path.

  Returns:
    A pattern object that will match repo-root-relative paths that refer
    to targets spec files. The pattern captures the repo-root-relative
    path to the directory that contains configs for a single builder
    (not just the targets spec files).
  """
  return re.compile(f'^({re.escape(builder_config_directory)}/([^/]+)/[^/]+)'
                    r'/targets/[^/]+\.json')


def RunSteps(api, properties):
  api.tryserver.require_is_tryserver()

  errors = VALIDATORS.validate(properties)
  if errors:
    return _result(
        status=common_pb.INFRA_FAILURE,
        elements=errors,
        header='The following errors were found with the input properties:')

  # Set the config on the gclient module rather than just making one and passing
  # it in because deapply_patch assumes that the gclient module's config is set
  api.gclient.set_config('chromium')
  checkout_root = api.path.cache_dir / 'builder'
  with api.context(cwd=checkout_root):
    update_result = api.bot_update.ensure_checkout(patch=True)

  repo_path = update_result.source_root.path

  with api.step.nest('determine affected targets spec files'):
    with api.context(cwd=repo_path):
      affected_files = set(api.tryserver.get_files_affected_by_patch(''))

  fake_precommit_details = generators.PrecommitDetails()
  precommit_details_by_builder_dir = {}

  targets_spec_file_re = _targets_spec_file_re(
      properties.builder_config_directory)
  for f in affected_files:
    if match := targets_spec_file_re.match(f):
      builder_dir = match.group(1)
      properties_file = f'{builder_dir}/properties.json'
      # The builder is either being deleted or switched to not being
      # bootstrapped. The latter case should be rare and wouldn't be easy to
      # distinguish, so just assume a deletion and don't try to verify the
      # builder.
      if (properties_file in affected_files and
          not api.path.exists(repo_path / properties_file)):
        continue
      bucket = match.group(2)
      precommit_details_by_builder_dir[builder_dir] = (
          fake_precommit_details
          if bucket in properties.precommit_buckets else None)

  if not precommit_details_by_builder_dir:
    api.step.empty('all affected builders are being deleted')
    return None

  starlark_config_by_builder_dir = {}
  with api.step.nest('get patched targets configs'):
    for builder_dir, precommit_details in (
        precommit_details_by_builder_dir.items()):
      starlark_config = _get_starlark_config(api, builder_dir, repo_path,
                                             checkout_root, precommit_details)
      if starlark_config:
        starlark_config_by_builder_dir[builder_dir] = starlark_config

  if not starlark_config_by_builder_dir:
    return None

  with api.context(cwd=checkout_root):
    api.bot_update.deapply_patch(update_result)

  # We need to check the existence of files with and without the patch, but the
  # path test API doesn't actually support that, so we need to get icky. File
  # states without the patch will be passed via the *without-patch-mock-paths*
  # property. To ensure that this can't be used in production, we check if we're
  # in a test.
  if api._test_data.enabled:
    mock_paths = api.properties.get('*without-patch-mock-paths*', {})
    for file_rel_path, exists in mock_paths.items():
      if exists:
        api.path.mock_add_paths(repo_path / file_rel_path)
      else:
        api.path.mock_remove_paths(repo_path / file_rel_path)

  failures = []
  for builder_dir, starlark_config in starlark_config_by_builder_dir.items():
    precommit_details = precommit_details_by_builder_dir[builder_dir]
    if not _verify_target_configs(api, builder_dir, repo_path,
                                  precommit_details, starlark_config):
      failures.append(builder_dir)
  if failures:
    return _result(
        status=common_pb.FAILURE,
        elements=failures,
        header='verification failed for the following builder directories:')


def _get_builder_config(
    api,
    builder_dir: str,
    repo_path: Path,
) -> ctbc.BuilderConfig | None:
  properties_json_path = repo_path.joinpath(builder_dir, 'properties.json')
  if not api.path.exists(properties_json_path):
    return None

  properties = api.file.read_json(
      'read properties file',
      properties_json_path,
      test_data={},
      include_log=True,
  )

  if _CTBC_PROPERTY not in properties:
    return None

  ctbc_properties = ctbc_properties_pb.InputProperties()
  json_format.ParseDict(properties[_CTBC_PROPERTY], ctbc_properties)
  return ctbc.proto.convert_builder_config(ctbc_properties.builder_config)


def _get_targets_config(
    api,
    step_name: str,
    builder_config: ctbc.BuilderConfig,
    source_dir: Path,
    targets_spec_dir: Path,
    precommit_details: generators.PrecommitDetails | None,
) -> targets_config_module.TargetsConfig:
  with api.step.nest(step_name):
    return api.chromium_tests.create_targets_config(
        builder_config,
        got_revisions={},
        source_dir=source_dir,
        build_dir=None,
        targets_spec_dir=targets_spec_dir,
        precommit_details=precommit_details,
        scripts_compile_targets_fn=lambda source_dir, build_dir: {},
    )


def _get_starlark_config(
    api,
    builder_dir: str,
    repo_path: Path,
    checkout_root: Path,
    precommit_details: generators.PrecommitDetails | None,
) -> targets_config_module.TargetsConfig | None:
  with api.step.nest(builder_dir) as presentation:
    builder_config = _get_builder_config(api, builder_dir, repo_path)
    if not builder_config:
      skip_reason = (
          "builder doesn't have bootstrapped builder config, can't verify")
    elif not builder_config.targets_spec_directory:
      skip_reason = (f'targets_spec_directory is not set in {_CTBC_PROPERTY},'
                     ' nothing to verify')
    else:
      return _get_targets_config(
          api,
          'get starlark targets config',
          builder_config,
          repo_path,
          checkout_root / builder_config.targets_spec_directory,
          precommit_details,
      )

    presentation.step_text = '\n' + skip_reason
    return None


def _verify_target_configs(
    api,
    builder_dir: str,
    repo_path: Path,
    precommit_details: generators.PrecommitDetails | None,
    starlark_config: targets_config_module.TargetsConfig,
) -> bool:
  with api.step.nest(f'verify {builder_dir}') as presentation:

    def success(message: str) -> bool:
      presentation.step_text = '\n' + message
      return True

    builder_config = _get_builder_config(api, builder_dir, repo_path)
    if not builder_config:
      return success(
          "builder didn't have bootstrapped builder config without patch,"
          " can't verify")

    if builder_config.targets_spec_directory:
      return success("builder is already using tests in starlark")

    chromium_config = api.chromium.make_config(builder_config.chromium_config)
    for c in builder_config.chromium_apply_config:
      api.chromium.apply_config(c, chromium_config)

    targets_spec_dir = repo_path / chromium_config.targets_spec_dir

    # If someone adds a builder with a new builder group and doesn't set tests
    # in the initial CL, then a subsequent CL that sets the tests would
    # encounter the pyl-generated targets spec file not existing without the
    # patch. So if any of the targets spec files don't exist, assume that the
    # builder wasn't running tests before and there's no need to verify
    # anything.
    spec_files_exist = [
        api.path.exists(targets_spec_dir / spec_file)
        for spec_file in builder_config.targets_spec_files.values()
    ]
    if not any(spec_files_exist):
      return success('none of the targets spec files for the builder exists,'
                     " assuming it hasn't been running tests")
    if not all(spec_files_exist):
      raise api.step.InfraFailure(
          'unexpected situation: not all of the targets spec files'
          f' exist without patch for {builder_dir}')

    pyl_config = _get_targets_config(
        api,
        'get pyl targets config',
        builder_config,
        repo_path,
        targets_spec_dir,
        precommit_details,
    )

    # If the builder isn't building targets or running tests without the patch,
    # then just allow targets for the builder to be newly set
    if not pyl_config.compile_only_targets and not pyl_config.all_tests:
      return success("builder wasn't running any tests before")

    diff = _compare_targets_configs(api, pyl_config, starlark_config)
    if not diff:
      return success('starlark config matches pyl config')

    presentation.logs['diff'] = diff
    presentation.status = api.step.FAILURE
    presentation.step_text = (
        "\nbuilder configs differ, see 'diff' log for details")
    return False


def _compare_targets_configs(
    api,
    pyl_config: targets_config_module.TargetsConfig,
    starlark_config: targets_config_module.TargetsConfig,
) -> Sequence[str] | None:

  def default_json_conversion(obj: object) -> object:
    if attr.has(obj):
      # Don't recurse, otherwise other attrs types will get converted to dicts
      # and we won't be given the opportunity to make type-specific tweaks to
      # child objects.
      d = attr.asdict(obj, recurse=False)

      # We really only care about the targets since the same builder config is
      # passed to both TargetsConfigs, so just return those
      if isinstance(obj, targets_config_module.TargetsConfig):
        return {
            str(builder_id): targets
            for builder_id, targets in d['_targets_by_builder_id'].items()
        }

      if isinstance(obj, chromium_tests.steps.AbstractTestSpec):
        d2 = {'_type': type(obj).__name__}
        d2.update(d)
        return d2

      return d

    # We don't care about the state of tests, just that they have equivalent
    # specs
    if isinstance(obj, chromium_tests.steps.TestWrapper):
      return obj._wrapper_spec
    if isinstance(obj, chromium_tests.steps.Test):
      return obj.spec

    if isinstance(obj, Path):
      return str(obj)

    if isinstance(obj, FrozenDict):
      return thaw(obj)

    raise TypeError(f'{obj!r} is not JSON serializable')  # pragma: no cover

  def convert_to_json(
      targets_config: targets_config_module.TargetsConfig) -> Sequence[str]:
    return api.json.dumps(
        targets_config,
        indent=2,
        sort_keys=False,
        default=default_json_conversion,
    ).splitlines()

  pyl_config_json = convert_to_json(pyl_config)
  starlark_config_json = convert_to_json(starlark_config)

  return list(
      difflib.unified_diff(
          pyl_config_json,
          starlark_config_json,
          fromfile='pyl targets config',
          tofile='starlark targets config',
          n=max(len(pyl_config_json), len(starlark_config_json)),
      ))


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
      non_existent_tester: str | None = None,
      non_existent_tester_group: str | None = None,
      try_bucket: str | None = None,
      try_builder: str | None = None,
      with_properties_file: bool = True,
      with_ctbc_property: bool = True,
      with_targets_spec_directory: bool = True,
      with_properties_file_without_patch: bool = True,
      with_targets_spec_directory_without_patch: bool = False,
      starlark_targets_spec: dict | None = None,
      testing_buildbot_targets_spec: dict | None = None,
  ) -> recipe_test_api.StepTestData:
    """Set necessary step test data for calling verify_builder_configs.

    Args:
      * bucket - The bucket of the builder to verify targets spec for or
        the bucket of the mirrored builder if try_bucket and try_builder
        are set.
      * builder - The builder to verify targets spec for or the mirrored
        builder if try_bucket and try_builder are set.
      * builder_group - The group of the builder to verify targets spec
        for or the group of the mirrored builder if try_bucket and
        try_builder are set.
      * non_existent_tester -  The name of a tester that there is no
        targets spec for without patch. Must be set iff
        non_existent_tester_group is set.
      * non_existent_tester_group - The builder group of the
        non-existent tester. Must be set iff non_existent_tester is set.
        Must not be equal to builder_group.
      * try_bucket - The bucket of the try builder to verify targets
        spec for. Must be set iff try_builder is set. Cannot be set if
        non_existent_tester is set.
      * try_builder - The try builder to verify targets spec for. Must
        be set iff try_builder is set.
      * with_ctbc_property - Whether or not the
        $build/chromium_tests_builder_config_property should be set for
        the builder.
      * with_targets_spec_directory - Whether or not the
        targets_spec_directory field should be set in the
        $build/chromium_tests_builder_config property.
      * starlark_targets_spec - The targets spec generated from starlark
      * testing_buildbot_targets_spec - The targets spec generated from
        //testing/buildbot. Cannot be set if non_existent_tester is set.
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

    builder_dir = f'{builder_config_dir}/{try_bucket or bucket}/{try_builder or builder}'
    t += api.tryserver.get_files_affected_by_patch(
        [f'{builder_dir}/targets/{builder_group}.json'],
        step_name=(
            'determine affected targets spec files.git diff to analyze patch'))

    get_targets_config_step = f'get patched targets configs.{builder_dir}'
    verify_step = f'verify {builder_dir}'

    existing_paths = []

    if with_properties_file:
      existing_paths.append(api.path.cache_dir /
                            f'builder/src/{builder_dir}/properties.json')
      if with_ctbc_property:
        if try_bucket:
          ctbc_prop = ctbc_api.properties_assembler_for_try_builder(
          ).with_mirrored_builder(
              bucket=bucket,
              builder=builder,
              builder_group=builder_group,
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  chromium_apply_config=['mb'],
              ),
          )
        else:
          ctbc_prop = ctbc_api.properties_assembler_for_ci_builder(
              bucket=bucket,
              builder=builder,
              builder_group=builder_group,
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  chromium_apply_config=['mb'],
              ),
          )
          if non_existent_tester_group:
            ctbc_prop.with_tester(
                bucket=bucket,
                builder=non_existent_tester,
                builder_group=non_existent_tester_group,
            )
        if with_targets_spec_directory:
          # See note in RunSteps about property
          t += api.properties(
              **{
                  '*without-patch-mock-paths*': {
                      f'{builder_dir}/properties.json':
                          with_properties_file_without_patch
                  }
              })
          if with_properties_file_without_patch:
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
      existing_paths.append(
          api.path.cache_dir /
          f'builder/src/testing/buildbot/{builder_group}.json')
      t += read_targets_spec(
          testing_buildbot_targets_spec,
          step_prefix=f'{verify_step}.get pyl targets config.')
    # In the case where there isn't the targets spec file for the tester, we
    # need to indicate that the targets spec file for the builder exists
    if non_existent_tester:
      existing_paths.append(
          api.path.cache_dir /
          f'builder/src/testing/buildbot/{builder_group}.json')

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
           'builder-config-dir/fake-bucket/fake-builder'),
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
           'builder-config-dir/fake-bucket/fake-builder'),
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
                      'builder-config-dir/fake-bucket/fake-builder'),
                     [(f'targets_spec_directory is not set in {_CTBC_PROPERTY},'
                       ' nothing to verify')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no-properties-file-without-patch',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
          with_properties_file_without_patch=False,
      ),
      api.post_check(
          post_process.StepTextContains,
          'verify builder-config-dir/fake-bucket/fake-builder',
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

  builder_dir = f'{builder_config_dir}/fake-bucket/fake-builder'
  yield api.test(
      'deleted-builder',
      api.buildbucket.try_build(),
      api.properties(
          targets_config_verifier_pb.InputProperties(
              builder_config_directory=builder_config_dir)),
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
