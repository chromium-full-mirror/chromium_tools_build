# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import recipe_engine.post_process as post

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_builtins_pgo
from RECIPE_MODULES.recipe_engine import buildbucket, properties


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  properties: properties.API
  v8_builtins_pgo: v8_builtins_pgo.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  properties: properties.TEST_API
  v8_builtins_pgo: v8_builtins_pgo.TEST_API


def RunSteps(api: DEPS):
  return api.v8_builtins_pgo.run(
      compilators=['x64'],
      swarming_service_account=api.properties.get('swarming_service_account'))


def GenTests(api: TEST_DEPS):
  pgo_api = api.v8_builtins_pgo

  def test(name,
           build,
           compilator_label,
           must_run_builders,
           do_not_run_builders,
           profiling_pool,
           additional=None):
    return api.test(
        name,
        build,
        *pgo_api.mock_compilation([compilator_label], ['x64']),
        *pgo_api.mock_profiles([compilator_label], ['x64']),
        api.post_process(
            post.MustRun,
            f'trigger compilators.{compilator_label} x64',
            'download benchmark code',
            f'collect compilation isolates.{compilator_label} x64',
            f'merge isolate with benchmark.{compilator_label} x64',
            f'trigger profilers.{compilator_label} x64',
            f'validate profiles.read profile for {compilator_label} x64',
        ),
        api.post_process(
            post.DoesNotRun,
            f'trigger compilators.{compilator_label} x86',
            'upload to gs',
            'assign pgo tags',
            'add_comment_to_gerrit_changes',
            'report exceptions.gsutil upload blocked-versions.txt',
        ),
        api.post_process(
            post.LogContains,
            f'trigger compilators.{compilator_label} x64.trigger compilator',
            'request',
            must_run_builders,
        ),
        api.post_process(
            post.LogDoesNotContain,
            f'trigger compilators.{compilator_label} x64.trigger compilator',
            'request',
            do_not_run_builders,
        ),
        api.post_process(
            post.StepCommandRE,
            (f'trigger profilers.{compilator_label} x64.'
             f'[trigger] pgo profile {compilator_label} x64 on Ubuntu 31.41'),
            [
                r'.*/swarming',
                'spawn-tasks',
                '-server',
                'https://example.swarmingserver.appspot.com',
                '-json-input',
                r'.*{' + f'"key": "pool", "value": "{profiling_pool}"' + r'}.*',
                '-json-output',
                '/path/to/tmp/json',
            ],
        ),
        *(additional or []),
        api.post_process(post.DropExpectation),
    )

  yield test(
      'ci',
      api.buildbucket.ci_build(bucket='ci', revision='c0ffee15'),
      compilator_label='c0ffee15',
      must_run_builders=['V8 Linux64 PGO instrumentation - builder'],
      do_not_run_builders=['v8_linux64_pgo_compile_rel'],
      profiling_pool='chromium.tests',
  )

  yield test(
      'ci_restricted',
      api.buildbucket.ci_build(bucket='ci', revision='c0ffee15'),
      compilator_label='c0ffee15',
      must_run_builders=['V8 Linux64 PGO instrumentation - builder'],
      do_not_run_builders=['v8_linux64_pgo_compile_rel'],
      profiling_pool='chromium.tests',
      additional=[
          api.properties(swarming_service_account='restricted@account.com'),
          api.post_process(
              post.StepCommandRE,
              ('trigger profilers.c0ffee15 x64.'
               '[trigger] pgo profile c0ffee15 x64 on Ubuntu 31.41'),
              [
                  r'.*/swarming',
                  'spawn-tasks',
                  '-server',
                  'https://example.swarmingserver.appspot.com',
                  '-json-input',
                  r'.*"service_account": "restricted@account.com".*',
                  '-json-output',
                  '/path/to/tmp/json',
              ],
          ),
      ],
  )

  yield test(
      'try',
      api.buildbucket.try_build(
          bucket='try', project='v8/v8', change_number=42, patch_set=3),
      compilator_label='crrev.com/c/42/3',
      must_run_builders=['v8_linux64_pgo_compile_rel'],
      do_not_run_builders=['V8 Linux64 PGO instrumentation - builder'],
      profiling_pool='chrome.tests',
  )

  yield test(
      'v8perf_try',
      api.buildbucket.try_build(
          bucket='try', project='v8/v8-perf', change_number=42, patch_set=3),
      compilator_label='8c8afc65',
      must_run_builders=['v8_linux64_pgo_compile_rel'],
      do_not_run_builders=['V8 Linux64 PGO instrumentation - builder'],
      profiling_pool='chrome.tests',
      additional=[
          pgo_api.mock_git_tags([('8c8afc650f556e3e', 'refs/heads/lkgr')]),
      ],
  )
