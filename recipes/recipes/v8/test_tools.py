# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for running tests for tools that don't run elsewhere yet.
Useful for tests that run too long for presubmit or that require
dependencies and need docker.
"""

from recipe_engine.post_process import DropExpectation, Filter

DEPS = [
  'chromium',
  'depot_tools/gclient',
  'infra/docker',
  'recipe_engine/context',
  'recipe_engine/path',
  'recipe_engine/step',
  'v8',
]


def RunSteps(api):
  api.gclient.set_config('v8')
  api.chromium.set_config('v8')
  api.v8.checkout()
  api.v8.runhooks()

  # Run node tests for js-fuzzer using the node docker image.
  with api.step.nest('js-fuzzer'):
    fuzzer_dir = api.path['checkout'].join('tools', 'clusterfuzz', 'js_fuzzer')
    docker_cmd = [
      'run',
      '--rm',
      '--name', 'dummy',
      '-v', '%s:/usr/src/app' % fuzzer_dir,
      '-w', '/usr/src/app',
      'node:10',
    ]
    with api.context(cwd=fuzzer_dir):
      api.docker.login(infra_step=True)
      api.docker(
          *(docker_cmd + ['npm', 'install']),
          step_name='npm install'
      )
      api.docker(
          *(docker_cmd + ['npm', 'test']),
          step_name='npm test'
      )


def GenTests(api):
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
