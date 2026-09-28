# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from PB.recipe_modules.build.devtools.examples.example import InputProperties
from recipe_engine.recipe_api import StepFailure

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_swarming, devtools
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
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
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium_swarming: chromium_swarming.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API


PROPERTIES = InputProperties


def RunSteps(api: DEPS, properties):
  assert api.devtools.get_affected_files() == []
  builder_config = properties.builder_config or 'Release'
  devtools_bundle = properties.devtools_bundle
  if 'devtools_bundle' not in api.properties:
    devtools_bundle = True

  api.devtools.configure(
    builder_config,
    is_official_build=True,
    force_host_cpu=properties.force_host_cpu or None,
    devtools_bundle=devtools_bundle,
  )
  api.devtools.update()

  with api.devtools.depot_on_path():
    build_dir = api.chromium.default_build_dir(api.devtools.source_dir)
    api.chromium.run_gn(api.devtools.source_dir, build_dir)
    api.step.empty(
      '{os} {cpu}'.format(**api.devtools.get_dimensions_for_platform())
    )
    if not properties.parallel:
      api.devtools.run_e2e(builder_config)
    else:
      with api.step.nest('E2E Tests'):
        commands = api.devtools.divided_e2e_commands(
          builder_config=builder_config,
        )
        cas_digest = api.devtools.archive_to_cas()
        tasks_results = []
        tasks = api.devtools.trigger_test_swarming_tasks(
          step_name='E2E Tests',
          cas_digest=cas_digest,
          commands=commands,
          rdb_test_type='e2e',
          run_phase='normal',
        )
        with api.step.nest('E2E Tests result collection'):
          with api.step.nest('E2E Tests shards results'):
            failed_shards = []
            for i in range(len(tasks)):
              step, is_valid = api.chromium_swarming.collect_task(tasks[i])
              tasks_results.append(step)
              if step.presentation.status != api.step.SUCCESS or not is_valid:
                failed_shards.append(i)
            if failed_shards:
              raise StepFailure(
                'Failure in shard(s) '
                + f'#{", ".join([str(x) for x in failed_shards])}.'
              )


def GenTests(api: TEST_DEPS):
  git_repo = 'https://chromium.googlesource.com/devtools/devtools-frontend'

  def try_build(builder='builder', **kwargs):
    return api.buildbucket.try_build(
      project='devtools',
      builder=builder,
      git_repo=git_repo,
      change_number=91827,
      patch_set=1,
      **kwargs,
    )

  def ci_build(builder='Builder'):
    return api.buildbucket.ci_build(
      project='devtools', builder=builder, git_repo=git_repo
    )

  yield api.test(
    'release',
    try_build(),
    status='SUCCESS',
  )

  yield api.test(
    'ci_release',
    ci_build(),
    api.post_process(post_process.DoesNotRun, 'upload screenshots'),
    api.post_process(post_process.DropExpectation),
    status='SUCCESS',
  )

  yield api.test(
    'parallel release',
    try_build(builder='parallel builder'),
    api.properties(parallel=True),
    api.properties(force_host_cpu='future_cpu'),
    api.step_data(
      'E2E Tests.divide test run',
      api.raw_io.stream_output_text(
        'ITERATIONS=1 node runner config pattern', stream='stdout'
      ),
    ),
    api.post_process(post_process.MustRun, 'E2E Tests'),
    api.post_process(post_process.DropExpectation),
    status='SUCCESS',
  )

  data = {
    'shards': [
      {
        'state': 'COMPLETED (FAILURE)',
      }
    ]
  }
  yield api.test(
    'failed parallel release',
    try_build(builder='parallel builder'),
    api.properties(parallel=True),
    api.step_data(
      'E2E Tests.divide test run',
      api.raw_io.stream_output_text(
        'node runner config pattern', stream='stdout'
      ),
    ),
    api.step_data(
      'E2E Tests.E2E Tests result collection.E2E Tests shards results.'
      + 'E2E Tests (Shard #0) on Ubuntu-22.04',
      api.chromium_swarming.summary(None, data),
    ),
    api.post_process(post_process.MustRun, 'E2E Tests'),
    api.post_process(post_process.DropExpectation),
    status='FAILURE',
  )

  yield api.test(
    'debug',
    api.properties(builder_config='Debug'),
    try_build(),
    api.post_process(post_process.DropExpectation),
    status='SUCCESS',
  )

  yield api.test(
    'clobber',
    api.properties(clobber=True),
    try_build(),
    api.post_process(post_process.DropExpectation),
    status='SUCCESS',
  )

  yield api.test(
    'mac arm64',
    try_build(),
    api.platform('mac', 64, 'arm'),
    api.post_process(post_process.MustRun, 'Mac-26 arm64'),
    api.post_process(post_process.DropExpectation),
    status='SUCCESS',
  )

  yield api.test(
    'no bundle',
    api.properties(devtools_bundle=False, parallel=True),
    try_build(builder='parallel builder'),
    api.step_data(
      'E2E Tests.divide test run',
      api.raw_io.stream_output_text(
        'ITERATIONS=1 node runner config pattern', stream='stdout'
      ),
    ),
    status='SUCCESS',
  )
