# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from PB.recipe_modules.build.chromium_compilator.properties import (
  InputProperties,
)

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build.chromium_tests.api import (
  ALL_TEST_BINARIES_ISOLATE_NAME,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
  code_coverage,
  filter as filter_module,
)
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  assertions,
  buildbucket,
  cq,
  path,
  properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  code_coverage: code_coverage.API
  cq: cq.API
  filter: filter_module.API
  path: path.API
  properties: properties.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  code_coverage: code_coverage.TEST_API
  cq: cq.TEST_API
  filter: filter_module.TEST_API
  path: path.TEST_API
  properties: properties.TEST_API
  tryserver: tryserver.TEST_API


PROPERTIES = InputProperties


def RunSteps(api: DEPS, properties):
  orchestrator = properties.orchestrator.builder_name
  builder_group = properties.orchestrator.builder_group
  orch_builder_id = chromium_types.BuilderId.create_for_group(
    builder_group, orchestrator
  )

  orch_builder_id = chromium_types.BuilderId.create_for_group(
    builder_group, orchestrator
  )

  _, orch_builder_config = api.chromium_tests_builder_config.lookup_builder(
    builder_id=orch_builder_id
  )
  api.chromium_tests.configure_build(orch_builder_config)

  update_result, build_dir, targets_config = (
    api.chromium_tests.prepare_checkout(orch_builder_config)
  )

  _, task = api.chromium_tests.build_affected_targets(
    orch_builder_id,
    orch_builder_config,
    update_result,
    build_dir,
    targets_config,
    isolate_output_files_for_coverage=True,
    additional_compile_targets=['infra_orchestrator:orchestrator_all'],
  )

  expected_tests = api.properties.get('expected_tests')
  if expected_tests is not None:
    tests = [t.name for t in task.test_suites]
    api.assertions.assertCountEqual(tests, expected_tests)


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  def ctbc_properties():
    return ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .with_mirrored_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .assemble()
    )

  yield api.test(
    'compilator_isolates_all_tests',
    api.code_coverage(use_clang_coverage=True),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-compilator',
    ),
    ctbc_properties(),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'scripts': [
            {
              "isolate_profile_data": True,
              "name": "check_static_initializers",
              "script": "check_static_initializers.py",
            }
          ],
        },
        'fake-tester': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.properties(
      InputProperties(
        orchestrator=InputProperties.Orchestrator(
          builder_name='fake-orchestrator', builder_group='fake-try-group'
        )
      )
    ),
    api.path.exists(
      api.path.cache_dir / 'builder/src/out/Release/browser_tests'
    ),
    api.post_process(
      post_process.StepCommandContains,
      'compile (with patch)',
      ['browser_tests', 'infra_orchestrator:orchestrator_all'],
    ),
    api.post_process(post_process.MustRun, 'isolate tests (with patch)'),
    api.post_process(
      post_process.LogContains,
      'isolate tests (with patch)',
      'json.output',
      [ALL_TEST_BINARIES_ISOLATE_NAME],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'compilator_isolates_skip_tests',
    api.code_coverage(use_clang_coverage=True),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-compilator',
      tags=api.buildbucket.tags(
        cq_equivalent_cl_group_key='12345', cq_attempt_key='67890'
      ),
    ),
    ctbc_properties(),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'scripts': [
            {
              "isolate_profile_data": True,
              "name": "check_static_initializers",
              "script": "check_static_initializers.py",
            }
          ],
        },
        'fake-tester': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'swarming': {},
            },
            {
              'name': 'more_tests',
              'swarming': {},
            },
          ],
        },
      },
    ),
    api.properties(
      InputProperties(
        orchestrator=InputProperties.Orchestrator(
          builder_name='fake-orchestrator', builder_group='fake-try-group'
        )
      )
    ),
    api.cq(run_mode='FULL_RUN'),
    api.chromium_tests.simulate_previous_build(
      test_statuses={'browser_tests': 'Success'}
    ),
    api.path.exists(
      api.path.cache_dir / 'builder/src/out/Release/browser_tests'
    ),
    api.post_process(
      post_process.StepCommandDoesNotContain,
      'compile (with patch)',
      ['browser_tests'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'compile (with patch)',
      ['infra_orchestrator:orchestrator_all', 'more_tests'],
    ),
    api.post_process(post_process.MustRun, 'isolate tests (with patch)'),
    api.post_process(
      post_process.LogContains,
      'isolate tests (with patch)',
      'json.output',
      [ALL_TEST_BINARIES_ISOLATE_NAME],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_compile_with_additional_compile_targets_kwarg',
    api.code_coverage(use_clang_coverage=True),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-compilator',
    ),
    ctbc_properties(),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'scripts': [
            {
              "isolate_profile_data": True,
              "name": "check_static_initializers",
              "script": "check_static_initializers.py",
            }
          ],
        },
        'fake-tester': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.filter.no_dependency(),
    api.properties(
      InputProperties(
        orchestrator=InputProperties.Orchestrator(
          builder_name='fake-orchestrator', builder_group='fake-try-group'
        )
      )
    ),
    api.post_process(post_process.DoesNotRun, 'compile (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'DEPS-only change returns non-isolated tests',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-compilator',
    ),
    api.properties(
      InputProperties(
        orchestrator=InputProperties.Orchestrator(
          builder_name='fake-orchestrator', builder_group='fake-try-group'
        )
      )
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(expected_tests=['checkdeps']),
    api.tryserver.get_files_affected_by_patch(['foo/DEPS']),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'scripts': [
            {
              "name": "checkdeps",
              "script": "checkdeps.py",
            }
          ],
        },
      },
    ),
    api.filter.no_dependency(),
    api.post_process(post_process.DoesNotRun, 'compile (with patch)'),
    api.post_process(post_process.DropExpectation),
  )
