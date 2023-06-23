# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Checks test configs specified in starlark versus testing/buildbot.

Once migration of all specs to starlark is complete, this recipe will be
irrelevant.
"""

import difflib
import re
from typing import Iterable, Optional, Sequence

import attr
from google.protobuf import json_format

from recipe_engine import post_process
from recipe_engine import recipe_test_api
from recipe_engine.config_types import Path
from recipe_engine.engine_types import FrozenDict, thaw

from RECIPE_MODULES.build import chromium_tests
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build import proto_validation

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
  return re.compile(f'^({re.escape(builder_config_directory)}/[^/]+/[^/]+)'
                    r'/targets/[^/]+\.json')


def RunSteps(api, properties):
  api.tryserver.require_is_tryserver()

  errors = VALIDATORS.validate(properties)
  if errors:
    return _result(
        status=common_pb.INFRA_FAILURE,
        elements=errors,
        header='The following errors were found with the input properties:')

  gclient_config = api.gclient.make_config('chromium')
  checkout_root = api.path['cache'].join('builder')
  with api.context(cwd=checkout_root):
    update_result = api.bot_update.ensure_checkout(
        patch=True, gclient_config=gclient_config)

  repo_path = checkout_root.join(update_result.json.output['root'])

  with api.step.nest('determine affected targets spec files'):
    with api.context(cwd=repo_path):
      affected_files = set(api.tryserver.get_files_affected_by_patch(''))

  affected_builder_dirs = set()
  targets_spec_file_re = _targets_spec_file_re(
      properties.builder_config_directory)
  for f in affected_files:
    if match := targets_spec_file_re.match(f):
      affected_builder_dirs.add(match.group(1))

  failures = []
  for builder_dir in affected_builder_dirs:
    if not _verify_targets_specs(api, checkout_root, repo_path, builder_dir):
      failures.append(builder_dir)
  if failures:
    return _result(
        status=common_pb.FAILURE,
        elements=failures,
        header='verification failed for the following builder directories:')


def _verify_targets_specs(api, checkout_root: Path, repo_path: Path,
                          builder_dir: str) -> bool:
  with api.step.nest(f'verify {builder_dir}') as presentation:

    def success(message: Optional[str] = None) -> bool:
      if message:
        presentation.step_text = '\n' + message
      return True

    def failure(message: str) -> bool:
      presentation.status = api.step.FAILURE
      presentation.step_text = '\n' + message
      return False

    properties = api.file.read_json(
        'read properties file',
        repo_path.join(builder_dir, 'properties.json'),
        test_data={},
        include_log=True,
    )

    if _CTBC_PROPERTY not in properties:
      return success(f'{_CTBC_PROPERTY} is not set, nothing to verify')

    ctbc_properties = ctbc_properties_pb.InputProperties()
    json_format.ParseDict(properties[_CTBC_PROPERTY], ctbc_properties)
    builder_config = ctbc.proto.convert_builder_config(
        ctbc_properties.builder_config)

    if not builder_config.targets_spec_directory:
      return success(f'targets_spec_directory is not set in {_CTBC_PROPERTY},'
                     ' nothing to verify')

    chromium_config = api.chromium.make_config(builder_config.chromium_config)
    for c in builder_config.chromium_apply_config:
      api.chromium.apply_config(c, chromium_config)

    def get_targets_config(step_name, targets_spec_dir):
      with api.step.nest(step_name):
        return api.chromium_tests.create_targets_config(
            builder_config,
            got_revisions={},
            checkout_path=repo_path,
            targets_spec_dir=targets_spec_dir,
        )

    pyl_config = get_targets_config('get pyl targets config',
                                    chromium_config.targets_spec_dir)
    starlark_config = get_targets_config(
        'get starlark targets config',
        checkout_root.join(builder_config.targets_spec_directory))

    diff = _compare_targets_configs(api, pyl_config, starlark_config)
    if diff:
      presentation.logs['diff'] = diff
      return failure("builder configs differ, see 'diff' log for details")

    return success('starlark config matches pyl config')


def _compare_targets_configs(
    api,
    pyl_config: chromium_tests.targets_config.TargetsConfig,
    starlark_config: chromium_tests.targets_config.TargetsConfig,
) -> Optional[Sequence[str]]:

  def default_json_conversion(obj: object) -> object:
    if attr.has(obj):
      # Don't recurse, otherwise other attrs types will get converted to dicts
      # and we won't be given the opportunity to make type-specific tweaks to
      # child objects.
      d = attr.asdict(obj, recurse=False)

      # We really only care about the targets since the same builder config is
      # passed to both TargetsConfigs, so just return those
      if isinstance(obj, chromium_tests.targets_config.TargetsConfig):
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
      targets_config: chromium_tests.targets_config.TargetsConfig,
  ) -> Sequence[str]:
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
          fromfile='recipe builder config',
          tofile='src-side builder config',
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
      with_ctbc_property: bool = True,
      with_targets_spec_directory: bool = True,
      starlark_targets_spec: Optional[object] = None,
      testing_buildbot_targets_spec: Optional[object] = None,
  ) -> recipe_test_api.StepTestData:
    """Set necessary step test data for calling verify_builder_configs.

    Args:
      * bucket - The bucket of the builder to verify targets spec for
      * builder - The builder to verify targets spec for
      * builder_group - The group of the builder to verify targets spec
        for
      * starlark_targets_spec - The targets spec generated from starlark
      * testing_buildbot_targets_spec - The targets spec generated from
        //testing/buildbot
    """
    t = api.buildbucket.try_build()
    t += api.properties(
        targets_config_verifier_pb.InputProperties(
            builder_config_directory=builder_config_dir))

    builder_dir = f'{builder_config_dir}/{bucket}/{builder}'
    t += api.tryserver.get_files_affected_by_patch(
        [f'{builder_dir}/targets/{builder_group}.json'],
        step_name=(
            'determine affected targets spec files.git diff to analyze patch'))

    verify_step = f'verify {builder_dir}'

    if with_ctbc_property:
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
      if with_targets_spec_directory:
        ctbc_prop = ctbc_prop.with_targets_spec_directory(
            f'{builder_dir}/targets')
      t += api.step_data(
          f'{verify_step}.read properties file',
          api.file.read_json({
              '$build/chromium_tests_builder_config':
                  json_format.MessageToDict(ctbc_prop.assemble()),
          }))

    def read_targets_spec(targets_spec, step_prefix):
      return api.chromium_tests.read_targets_spec(
          builder_group, {builder: targets_spec}, step_prefix=step_prefix)

    if starlark_targets_spec:
      t += read_targets_spec(
          starlark_targets_spec,
          step_prefix=f'{verify_step}.get starlark targets config.')
    if testing_buildbot_targets_spec:
      t += read_targets_spec(
          testing_buildbot_targets_spec,
          step_prefix=f'{verify_step}.get pyl targets config.')

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
      'no-ctbc-property',
      test_data(
          bucket='fake-bucket',
          builder='fake-builder',
          builder_group='fake-group',
          with_ctbc_property=False,
      ),
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
      api.post_process(post_process.DropExpectation),
  )

  # Must appear last since it drops expectations
  def invalid_properties(*errors):
    test_data = api.expect_status('INFRA_FAILURE')
    test_data += api.post_check(
        post_process.ResultReasonRE,
        '^The following errors were found with the input properties')
    for error in errors:
      test_data += api.post_check(post_process.ResultReasonRE, error)
    test_data += api.post_process(post_process.DropExpectation)
    return test_data

  yield api.test(
      'builder-config-dir-not-set',
      api.buildbucket.try_build(),
      invalid_properties('builder_config_directory is not set'),
  )
