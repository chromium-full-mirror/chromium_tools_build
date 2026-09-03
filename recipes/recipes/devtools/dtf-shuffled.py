# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from recipe_engine.post_process import (DoesNotRun, DropExpectation, Filter,
                                        MustRun, SummaryMarkdown)
from PB.recipes.build.devtools.dtf_shuffled import InputProperties

from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.e2e_tests_runner import E2ETests, RepeatE2EShuffledTests
from RECIPE_MODULES.build.devtools.test_phases import run_test_pipelines

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    builder_group,
    chromium,
    chromium_swarming,
    devtools,
    v8,
)
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cas,
    context,
    file,
    futures,
    path,
    properties,
    random,
    resultdb,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  cas: cas.API
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  context: context.API
  devtools: devtools.API
  file: file.API
  futures: futures.API
  path: path.API
  properties: properties.API
  random: random.API
  resultdb: resultdb.API
  step: step.API
  tryserver: tryserver.API
  v8: v8.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  builder_group: builder_group.TEST_API

PROPERTIES = InputProperties


def RunSteps(api: DEPS, properties):
  builder_config = 'Release'
  is_official_build = False
  devtools_skip_typecheck = False
  api.devtools.configure(builder_config, is_official_build,
                         devtools_skip_typecheck)
  api.devtools.update()

  build_dir = api.devtools.source_dir / 'out' / api.chromium.c.build_config_fs
  with api.devtools.depot_on_path():
    api.devtools.clean_out_dir(builder_config, properties.clobber)
    with api.chromium.guard_compile(build_dir):
      api.chromium.run_gn(api.devtools.source_dir, build_dir)

      compilation_result = api.chromium.compile(api.devtools.source_dir,
                                                build_dir)
      if compilation_result.status != common_pb.SUCCESS:
        return compilation_result
    cas_digest = api.devtools.archive_to_cas()

    trigger = SwarmingTrigger(api, cas_digest)
    tests = [
        E2ETests(api, trigger, builder_config, 'E2E Tests'),
        RepeatE2EShuffledTests(api, trigger, builder_config,
                               'Repeat E2E Tests'),
    ]

    results = run_test_pipelines(api, tests)

    return results.raw_result()


def GenTests(api: TEST_DEPS):
  git_repo = 'https://chromium.googlesource.com/devtools/devtools-frontend'

  def ci_build(builder):
    return api.buildbucket.ci_build(
        project='devtools', builder=builder, git_repo=git_repo)

  yield api.test(
      'compile failure',
      api.builder_group.for_current('devtools-frontend'),
      ci_build(builder='linux'),
      api.step_data('compile', retcode=1),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'basic',
      api.builder_group.for_current('tryserver.devtools-frontend-shuffled'),
      ci_build(builder='linux'),
  )
