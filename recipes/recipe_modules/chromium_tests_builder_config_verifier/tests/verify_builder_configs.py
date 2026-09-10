# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import attr
from google.protobuf import json_format

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium_tests_builder_config,
  chromium_tests_builder_config_verifier,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  json,
  path,
  properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  chromium_tests_builder_config_verifier: (
    chromium_tests_builder_config_verifier.API
  )
  json: json.API
  path: path.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  chromium_tests_builder_config_verifier: (
    chromium_tests_builder_config_verifier.TEST_API
  )
  json: json.TEST_API
  properties: properties.TEST_API


PROPERTIES = {
  'dbs': Property(default=()),
}

_PROPS_DIR = 'props-files'


def RunSteps(api: DEPS, dbs):
  # We need some fake path to use as the repo path
  repo_path = api.path.start_dir

  return api.chromium_tests_builder_config_verifier.verify_builder_configs(
    repo_path,
    _PROPS_DIR,
    dbs,
    try_buckets=api.properties.get('try_buckets', []),
  )


def GenTests(api: TEST_DEPS):

  def dumps(obj):
    return api.json.dumps(obj, indent=2)

  def check_verify(
    check, steps, step_name, status='SUCCESS', step_text=None, has_log=None
  ):
    if check('step {} was run'.format(step_name), step_name in steps):
      step = steps[step_name]
      if check(
        'step {} has expected status'.format(step_name), step.status == status
      ):
        if step_text is not None:
          message = 'step_text for step {} contains expected string'.format(
            step_name
          )
          check(message, step_text in step.step_text)
        if has_log is not None:
          check(
            'step {} has log {}'.format(step_name, has_log),
            has_log in step.logs,
          )

  def build_failure_result(check, steps, *files):
    return post_process.SummaryMarkdown(
      check,
      steps,
      '\n* '.join(['Could not verify the following files:\n'] + list(files)),
    )

  ctbcv_api = api.chromium_tests_builder_config_verifier

  yield api.test(
    'non-properties-file',
    api.buildbucket.try_build(),
    ctbcv_api.test_case(affected_files=['foo/bar/non-properties-file']),
    api.post_check(
      post_process.DoesNotRun, 'verify foo/bar/non-properties-file'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'unaffected-properties-files',
    api.buildbucket.try_build(),
    ctbcv_api.test_case(
      properties_files_directory=_PROPS_DIR,
      properties_files={
        f'{_PROPS_DIR}/bucket/unchanged/properties.json': ctbcv_api.Contents(
          patched='{}', at_head='{}'
        ),
      },
    ),
    api.post_check(
      post_process.DoesNotRun,
      f'verify {_PROPS_DIR}/bucket/unchanged/properties.json',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'properties-file-without-builder-config',
    api.buildbucket.try_build(),
    ctbcv_api.test_case(
      properties_files_directory=_PROPS_DIR,
      properties_files={
        f'{_PROPS_DIR}/bucket/no-builder-config/properties.json': ctbcv_api.Contents(
          patched='{}'
        ),
      },
    ),
    api.post_check(
      check_verify,
      f'verify {_PROPS_DIR}/bucket/no-builder-config/properties.json',
      step_text=(
        '$build/chromium_tests_builder_config is not set, nothing to verify'
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'unchanged-builder-config',
    api.buildbucket.try_build(),
    ctbcv_api.test_case(
      properties_files_directory=_PROPS_DIR,
      properties_files={
        f'{_PROPS_DIR}/bucket/same-builder-config/properties.json': ctbcv_api.Contents(
          # We only evaluate it if its changed and there's a recipe
          # config to compare against, so it doesn't need to be valid
          patched=dumps(
            {
              '$build/chromium_tests_builder_config': {
                'foo': 'bar',
              },
              'baz': 'shaz',
            }
          ),
          at_head=dumps(
            {
              '$build/chromium_tests_builder_config': {
                'foo': 'bar',
              },
            }
          ),
        ),
      },
    ),
    api.post_check(
      check_verify,
      f'verify {_PROPS_DIR}/bucket/same-builder-config/properties.json',
      step_text=(
        '$build/chromium_tests_builder_config is unchanged, nothing to verify'
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'changed-builder-config-no-builder-group',
    api.buildbucket.try_build(),
    ctbcv_api.test_case(
      properties_files_directory=_PROPS_DIR,
      properties_files={
        # We only evaluate it if its changed and there's a recipe config
        # to compare against, so it doesn't need to be valid
        f'{_PROPS_DIR}/bucket/no-builder-group/properties.json': ctbcv_api.Contents(
          patched=dumps(
            {
              '$build/chromium_tests_builder_config': {
                'foo': 'bar',
              },
            }
          )
        ),
      },
    ),
    api.post_check(
      check_verify,
      f'verify {_PROPS_DIR}/bucket/no-builder-group/properties.json',
      status='FAILURE',
      step_text="builder_group property is not set, can't verify",
    ),
    api.expect_status('FAILURE'),
    api.post_check(
      build_failure_result,
      f'{_PROPS_DIR}/bucket/no-builder-group/properties.json',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'changed-builder-config-without-recipe-config',
    api.buildbucket.try_build(),
    ctbcv_api.test_case(
      properties_files_directory=_PROPS_DIR,
      properties_files={
        # We only evaluate it if its changed and there's a recipe config
        # to compare against, so it doesn't need to be valid
        f'{_PROPS_DIR}/bucket/no-recipe-config/properties.json': ctbcv_api.Contents(
          patched=dumps(
            {
              '$build/chromium_tests_builder_config': {
                'foo': 'bar',
              },
              'builder_group': 'fake-group',
            }
          )
        ),
      },
    ),
    api.post_check(
      check_verify,
      f'verify {_PROPS_DIR}/bucket/no-recipe-config/properties.json',
      step_text='no recipe config exists, nothing to verify',
    ),
    api.post_process(post_process.DropExpectation),
  )

  example_spec = ctbc.BuilderSpec.create(
    chromium_config='chromium',
    gclient_config='chromium',
  )

  ctbc_api = api.chromium_tests_builder_config

  ctbc_prop = (
    ctbc_api.properties_assembler_for_ci_builder(
      bucket='bucket',
      builder='matching-config',
      builder_group='fake-group',
      builder_spec=attr.evolve(example_spec, perf_isolate_upload=True),
    )
    .with_tester(
      builder='matching-config-tester',
      builder_group='fake-group',
      builder_spec=example_spec,
    )
    .assemble()
  )

  yield api.test(
    'changed-builder-config-matching-recipe-config',
    api.buildbucket.try_build(),
    ctbcv_api.test_case(
      properties_files_directory=_PROPS_DIR,
      properties_files={
        f'{_PROPS_DIR}/bucket/matching-config/properties.json': ctbcv_api.Contents(
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
    api.properties(
      dbs=[
        (
          ctbc.BuilderDatabase.create(
            {
              'fake-group': {
                'matching-config': attr.evolve(
                  example_spec,
                  simulation_platform='linux',
                  chromium_config_kwargs={'HOST_PLATFORM': 'linux'},
                  perf_isolate_upload=True,
                ),
                'matching-config-tester': attr.evolve(
                  example_spec,
                  execution_mode=ctbc.TEST,
                  parent_buildername='matching-config',
                ),
              },
              # The recipe config will have the entire builder DB, so this
              # ensures that the verification accounts for that
              'unrelated-group': {
                'unrelated-builder': example_spec,
              },
            }
          ),
          None,
        )
      ]
    ),
    api.post_check(
      check_verify,
      f'verify {_PROPS_DIR}/bucket/matching-config/properties.json',
      step_text='src-side config matches recipe config',
    ),
    api.post_process(post_process.DropExpectation),
  )

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
    'changed-builder-config-matching-recipe-config-after-first-lookup',
    api.buildbucket.try_build(),
    ctbcv_api.test_case(
      properties_files_directory=_PROPS_DIR,
      properties_files={
        f'{_PROPS_DIR}/bucket/matching-config/properties.json': ctbcv_api.Contents(
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
    api.properties(
      dbs=[
        (ctbc.BuilderDatabase.create({}), None),
        (
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
          ),
          None,
        ),
      ]
    ),
    api.post_check(
      check_verify,
      f'verify {_PROPS_DIR}/bucket/matching-config/properties.json',
      step_text='src-side config matches recipe config',
    ),
    api.post_process(post_process.DropExpectation),
  )

  ctbc_prop = (
    ctbc_api.properties_assembler_for_ci_tester(
      bucket='bucket',
      builder='matching-config-tester',
      builder_group='fake-group',
      builder_spec=example_spec,
    )
    .with_parent(
      builder='matching-config',
      builder_group='fake-group',
    )
    .assemble()
  )

  yield api.test(
    'changed-tester-config-matching-recipe-config',
    api.buildbucket.try_build(),
    ctbcv_api.test_case(
      properties_files_directory=_PROPS_DIR,
      properties_files={
        f'{_PROPS_DIR}/bucket/matching-config-tester/properties.json': ctbcv_api.Contents(
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
    api.properties(
      dbs=[
        (
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
          ),
          None,
        )
      ]
    ),
    api.post_check(
      check_verify,
      f'verify {_PROPS_DIR}/bucket/matching-config-tester/properties.json',
      step_text='src-side config matches recipe config',
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
    ctbcv_api.test_case(
      properties_files_directory=_PROPS_DIR,
      properties_files={
        # We only evaluate it if its changed and there's a recipe config
        # to compare against, so it doesn't need to be valid
        f'{_PROPS_DIR}/bucket/not-matching-config/properties.json': ctbcv_api.Contents(
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
    api.properties(
      dbs=[
        (
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
          ),
          None,
        )
      ]
    ),
    api.post_check(
      check_verify,
      f'verify {_PROPS_DIR}/bucket/not-matching-config/properties.json',
      status='FAILURE',
      step_text="builder configs differ, see 'diff' log for details",
      has_log='diff',
    ),
    api.post_check(
      build_failure_result,
      f'{_PROPS_DIR}/bucket/not-matching-config/properties.json',
    ),
    # Keep just the verify step so that we can see when the diff changes
    api.post_process(
      post_process.Filter(
        f'verify {_PROPS_DIR}/bucket/not-matching-config/properties.json'
      )
    ),
    api.expect_status('FAILURE'),
  )

  # Some internal builders reuse the CI builder group for try builders
  yield api.test(
    'shared-builder-id',
    api.buildbucket.try_build(),
    api.properties(try_buckets=['fake-try-bucket']),
    api.properties(
      dbs=[
        (
          ctbc.BuilderDatabase.create(
            {
              'fake-group': {
                'fake-builder': ctbc.BuilderSpec.create(
                  chromium_config='chromium',
                  gclient_config='chromium',
                ),
              },
            }
          ),
          ctbc.TryDatabase.create(
            {
              'fake-group': {
                'fake-builder': ctbc.TrySpec.create_for_single_mirror(
                  builder_group='fake-group',
                  buildername='fake-builder',
                ),
              },
            }
          ),
        )
      ]
    ),
    ctbcv_api.test_case(
      properties_files_directory=_PROPS_DIR,
      properties_files={
        f'{_PROPS_DIR}/fake-ci-bucket/fake-builder/properties.json': ctbcv_api.Contents(
          patched=dumps(
            {
              '$build/chromium_tests_builder_config': json_format.MessageToDict(
                ctbc_api.properties_assembler_for_ci_builder(
                  bucket='fake-ci-bucket',
                  builder='fake-builder',
                  builder_group='fake-group',
                  builder_spec=example_spec,
                )
                .with_mirroring_builder(
                  builder_group='fake-group',
                  builder='fake-builder',
                )
                .assemble()
              ),
              'builder_group': 'fake-group',
            }
          )
        ),
        f'{_PROPS_DIR}/fake-try-bucket/fake-builder/properties.json': ctbcv_api.Contents(
          patched=dumps(
            {
              '$build/chromium_tests_builder_config': json_format.MessageToDict(
                ctbc_api.properties_assembler_for_try_builder()
                .with_mirrored_builder(
                  bucket='fake-ci-bucket',
                  builder='fake-builder',
                  builder_group='fake-group',
                  builder_spec=example_spec,
                )
                .assemble()
              ),
              'builder_group': 'fake-group',
            }
          )
        ),
      },
    ),
    api.post_check(
      check_verify,
      f'verify {_PROPS_DIR}/fake-ci-bucket/fake-builder/properties.json',
      step_text='src-side config matches recipe config',
    ),
    api.post_check(
      check_verify,
      f'verify {_PROPS_DIR}/fake-try-bucket/fake-builder/properties.json',
      step_text='src-side config matches recipe config',
    ),
    api.post_process(post_process.DropExpectation),
  )
