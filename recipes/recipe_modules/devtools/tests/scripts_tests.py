# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.scripts_tests_runner import ScriptsTests

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_swarming, devtools
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  file,
  path,
  platform,
  properties,
  raw_io,
  resultdb,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  devtools: devtools.API
  file: file.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  properties: properties.TEST_API
  tryserver: tryserver.TEST_API


def RunSteps(api: DEPS):
  builder_config = api.properties.get('builder_config', 'Release')
  target_os = api.properties.get('target_os', 'ubuntu')
  api.devtools.configure(builder_config, is_official_build=False)
  api.devtools.update()

  trigger = SwarmingTrigger(api, '1234567/890')
  runner = ScriptsTests(
    api, trigger, builder_config, step_name='Scripts Tests', target_os=target_os
  )

  if not runner.skip():
    runner.trigger()
    runner.process_results()


def GenTests(api: TEST_DEPS):
  git_repo = 'https://chromium.googlesource.com/devtools/devtools-frontend'

  def try_build(builder='linux', **kwargs):
    return api.buildbucket.try_build(
      project='devtools',
      builder=builder,
      git_repo=git_repo,
      change_number=91827,
      patch_set=1,
      **kwargs,
    )

  def ci_build(builder='linux', git_ref='refs/heads/main'):
    return api.buildbucket.ci_build(
      project='devtools',
      builder=builder,
      git_repo=git_repo,
      git_ref=git_ref,
    )

  def command_log(suites):
    return '\n'.join(
      [
        'vpython3',
        '-u',
        'third_party/node/node.py',
        '--output',
        'scripts/run_on_target.mjs',
        '--target=Release',
        'gen/test/run.js',
        '--skip-ninja',
        *suites,
      ]
    )

  base_suites = [
    'scripts/eslint_rules/tests/',
    'scripts/stylelint_rules/tests/',
    'scripts/build/tests/',
  ]
  all_suites = [
    *base_suites,
    'scripts/ai_assistance/',
    'test/ai_evals/report/',
  ]

  yield api.test('basic', try_build())
  yield api.test(
    'skip_non_linux',
    try_build(),
    api.properties(target_os='mac'),
    api.post_process(post_process.DoesNotRun, 'Trigger Scripts Tests'),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'skip_debug',
    try_build(),
    api.properties(builder_config='Debug'),
    api.post_process(post_process.DoesNotRun, 'Trigger Scripts Tests'),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'm159_release_branch',
    try_build(),
    api.tryserver.gerrit_change_target_ref('refs/heads/chromium/8154'),
    api.post_process(
      post_process.LogEquals,
      'Trigger Scripts Tests.Full command',
      'command',
      command_log(base_suites),
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'try_target_ref_precedence',
    try_build(revision='a' * 40, git_ref='refs/heads/main'),
    api.tryserver.gerrit_change_target_ref('refs/heads/chromium/8154'),
    api.post_process(
      post_process.LogEquals,
      'Trigger Scripts Tests.Full command',
      'command',
      command_log(base_suites),
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'm159_respin_mini_branch',
    try_build(),
    api.tryserver.gerrit_change_target_ref('refs/heads/chromium/8154_1'),
    api.post_process(
      post_process.LogEquals,
      'Trigger Scripts Tests.Full command',
      'command',
      command_log(base_suites),
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'cutoff_ci_branch_heads',
    ci_build(git_ref='refs/branch-heads/8158'),
    api.post_process(
      post_process.LogEquals,
      'Trigger Scripts Tests.Full command',
      'command',
      command_log(base_suites),
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'post_cutoff_release_branch',
    try_build(),
    api.tryserver.gerrit_change_target_ref('refs/heads/chromium/8159'),
    api.post_process(
      post_process.LogEquals,
      'Trigger Scripts Tests.Full command',
      'command',
      command_log(all_suites),
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'empty_ref_ci_build',
    ci_build(git_ref=''),
    api.post_process(
      post_process.LogEquals,
      'Trigger Scripts Tests.Full command',
      'command',
      command_log(all_suites),
    ),
    api.post_process(post_process.DropExpectation),
  )
