# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.performance_tests_runner import PerformanceTests

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


def RunSteps(api: DEPS):
  builder_config = api.properties.get('builder_config', 'Release')
  target_os = api.properties.get('target_os', 'ubuntu')
  api.devtools.configure(
      builder_config, is_official_build=False, devtools_skip_typecheck=True)
  api.devtools.update()

  trigger = SwarmingTrigger(api, '1234567/890')
  runner = PerformanceTests(
      api,
      trigger,
      builder_config,
      step_name='Performance Tests',
      target_os=target_os)

  if not runner.skip():
    runner.trigger()
    runner.process_results()


def GenTests(api: TEST_DEPS):
  git_repo = 'https://chromium.googlesource.com/devtools/devtools-frontend'

  def try_build(builder='linux'):
    return api.buildbucket.try_build(
        project='devtools',
        builder=builder,
        git_repo=git_repo,
        change_number=91827,
        patch_set=1)

  yield api.test('basic', try_build())
  yield api.test(
      'perf_benchmarks',
      try_build(),
      api.properties(perf_benchmarks=True),
  )
  yield api.test(
      'skip_non_linux',
      try_build(),
      api.properties(target_os='mac'),
      api.post_process(post_process.DoesNotRun, 'Trigger Performance Tests'),
      api.post_process(post_process.DropExpectation),
  )
