# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for running tests for tools that don't run elsewhere yet.
Useful for tests that run too long for presubmit or that require
dependencies and need docker.
"""

from recipe_engine.post_process import DropExpectation, Filter

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, v8
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.infra import docker
from RECIPE_MODULES.recipe_engine import context, step


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  context: context.API
  docker: docker.API
  gclient: gclient.API
  step: step.API
  v8: v8.API


def RunSteps(api: DEPS):
  api.gclient.set_config('v8')
  api.chromium.set_config('v8')
  update_result = api.v8.checkout()
  source_dir = update_result.source_root.path
  build_dir = api.v8.build_dir(source_dir)
  api.v8.runhooks(source_dir, build_dir)

  # Run node tests for js-fuzzer using the node docker image.
  source_dir = update_result.source_root.path
  with api.step.nest('js-fuzzer'):
    fuzzer_dir = source_dir.joinpath('tools', 'clusterfuzz', 'js_fuzzer')
    docker_cmd = [
      'run',
      '--rm',
      '--name',
      'dummy',
      '--security-opt',
      'no-new-privileges',
      '-v',
      '%s:/usr/src/app' % fuzzer_dir,
      '-w',
      '/usr/src/app',
    ]
    with api.context(cwd=fuzzer_dir):
      api.docker.login(infra_step=True)
      api.docker(
        *(docker_cmd + ['node:23', 'npm', 'install']), step_name='npm install'
      )
      api.docker(
        *(docker_cmd + ['--network=none', 'node:23', 'npm', 'test']),
        step_name='npm test',
      )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.post_process(Filter('js-fuzzer.npm install', 'js-fuzzer.npm test')),
    status='SUCCESS',
  )

  yield api.test(
    'login failure',
    api.override_step_data('js-fuzzer.docker login', retcode=1),
    api.post_process(DropExpectation),
    status='INFRA_FAILURE',
  )
