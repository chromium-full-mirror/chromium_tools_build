# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Triggers compilator and tests"""

from recipe_engine import post_process

from PB.recipe_modules.build.chromium_orchestrator.properties import (
  InputProperties,
)

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_orchestrator,
  chromium_tests_builder_config,
  chromium_turboci,
  code_coverage,
)
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cas,
  cv,
  json,
  platform,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  cas: cas.API
  chromium: chromium.API
  chromium_orchestrator: chromium_orchestrator.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  chromium_turboci: chromium_turboci.API
  code_coverage: code_coverage.API
  cv: cv.API
  json: json.API
  platform: platform.API
  properties: properties.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_orchestrator: chromium_orchestrator.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  code_coverage: code_coverage.TEST_API
  cv: cv.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  api.tryserver.require_is_tryserver()

  with (
    api.chromium.chromium_layout(),
    api.chromium_turboci.display_turboci_checks(),
  ):
    return api.chromium_orchestrator.trybot_steps()


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
    'basic',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-orchestrator',
      tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
    ),
    ctbc_api.properties(
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
    ),
    api.cv(run_mode='FULL_RUN'),
    api.properties(
      **{
        '$build/chromium_orchestrator': InputProperties(
          compilator='fake-compilator',
          compilator_watcher_git_revision='e841fc',
        ),
      }
    ),
    api.code_coverage(use_clang_coverage=True),
    api.chromium_orchestrator.override_compilator_steps(),
    api.chromium_orchestrator.override_compilator_steps(is_compile_phase=False),
    api.chromium_orchestrator.override_test_spec(
      builder_group='fake-group',
      builder='fake-builder',
      tester='fake-tester',
    ),
    api.post_process(
      post_process.StepCommandContains,
      'install infra/chromium/compilator_watcher.ensure_installed',
      [
        '-ensure-file',
        'infra/chromium/compilator_watcher/${platform} git_revision:e841fc',
      ],
    ),
    api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
    api.post_process(post_process.MustRun, 'browser_tests (with patch)'),
    api.post_process(
      post_process.MustRun, 'downloading cas digest all_test_binaries'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'mac-on-linux',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-orchestrator',
      tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
    ),
    api.platform('linux', 64),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
          chromium_config_kwargs={
            'TARGET_PLATFORM': 'mac',
          },
        ),
      )
      .assemble()
    ),
    api.cv(run_mode='FULL_RUN'),
    api.properties(
      **{
        '$build/chromium_orchestrator': InputProperties(
          compilator='fake-compilator',
          compilator_watcher_git_revision='e841fc',
        ),
      }
    ),
    api.code_coverage(use_clang_coverage=True),
    api.chromium_orchestrator.override_compilator_steps(),
    api.chromium_orchestrator.override_compilator_steps(is_compile_phase=False),
    api.chromium_orchestrator.override_test_spec(
      builder_group='fake-group',
      builder='fake-builder',
    ),
    api.post_process(
      post_process.StepCommandContains,
      'install infra/chromium/compilator_watcher.ensure_installed',
      [
        '-ensure-file',
        'infra/chromium/compilator_watcher/${platform} git_revision:e841fc',
      ],
    ),
    api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
    api.post_process(post_process.MustRun, 'browser_tests (with patch)'),
    api.post_process(
      post_process.MustRun, 'downloading cas digest all_test_binaries'
    ),
    api.post_process(post_process.DropExpectation),
  )
