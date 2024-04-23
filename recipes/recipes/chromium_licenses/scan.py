# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to scan chromium/src for license errors."""

import re

from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]

CHROMIUM_REPO_URL = 'https://chromium.googlesource.com/chromium/src.git'


def RunSteps(api):
  chrome_dir = api.path.cache_dir.joinpath('builder', 'chrome')
  api.file.ensure_directory('chrome dir', chrome_dir)

  # Add Chromium to the source checkout
  gclient_config = api.gclient.make_config(CACHE_DIR=chrome_dir)
  soln = gclient_config.solutions.add()
  soln.name = 'src'
  soln.url = CHROMIUM_REPO_URL
  soln.revision = 'origin/main'
  soln.custom_vars = {
      'checkout_android': True,
      'checkout_fuchsia_no_hooks': True
  }
  api.gclient.c = gclient_config

  env = {}
  if api.platform.is_win:
    env['DEPOT_TOOLS_WIN_TOOLCHAIN_ROOT'] = (
        api.path.cache_dir / 'win_toolchain')
    env['DEPOT_TOOLS_WIN_TOOLCHAIN'] = 0

  with api.context(cwd=chrome_dir, env=env):
    # Update Chrome source in the builder cache. cache/builder is
    # available on all bots by default and is preserved between tasks.
    # It is not shared amongst all bots in the pool, or network mounted,
    # but bots which already have the named cache are preferred when
    # running recipes which use them.
    api.bot_update.ensure_checkout(with_tags=False)
    api.gclient.runhooks()

    # Run the licenses tool from the src directory so the special cases
    # can apply correctly.
    chrome_src = api.path.cache_dir.joinpath('builder', 'chrome', 'src')
    api.file.ensure_directory('chrome src dir', chrome_src)

    with api.context(cwd=chrome_src, env=env):
      with api.step.nest('scan for license errors'):
        license_tool = api.path.join(chrome_src, 'tools', 'licenses',
                                     'licenses.py')
        step_result = api.step(
            'run licenses tool', [license_tool, 'scan'],
            stdout=api.raw_io.output_text(),
            ok_ret=(0, 1))

        output = step_result.stdout.splitlines()
        step_result.presentation.logs['stdout'] = output

        # Process the script output for license errors.
        errors = 0
        for line in output:
          if re.match(r'^Errors.*$', line):
            errors += 1
        status = api.step.FAILURE if errors else api.step.SUCCESS
        api.step.empty(
            'summary of results',
            status=status,
            step_text=f'Found {errors} README files with license errors.',
        )


def GenTests(api):
  build = build_pb2.Build(
      infra=build_pb2.BuildInfra(
          swarming=build_pb2.BuildInfra.Swarming(task_dimensions=[
              common_pb2.RequestedDimension(key="os", value="linux")
          ]),))

  yield api.test(
      'no license errors',
      api.buildbucket.build(build),
      api.step_data(
          'scan for license errors.run licenses tool',
          stdout=api.raw_io.output_text(
              'debug info that is not a license error\n')),
      api.post_process(post_process.StepTextEquals,
                       'scan for license errors.summary of results',
                       'Found 0 README files with license errors.'),
      api.post_process(post_process.StepSuccess, 'scan for license errors'),
      api.expect_status('SUCCESS'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'license errors',
      api.buildbucket.build(build),
      api.step_data(
          'scan for license errors.run licenses tool',
          stdout=api.raw_io.output_text(
              'Errors: /file/path/to/readme: bad things in here\n'
              '\n'
              'debug info that is not a license error\n'
              'Errors: license error details\n'
              '\n')),
      api.post_process(post_process.StepTextEquals,
                       'scan for license errors.summary of results',
                       'Found 2 README files with license errors.'),
      api.post_process(post_process.StepFailure, 'scan for license errors'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'windows',
      api.buildbucket.build(build),
      api.platform('win', 64),
      api.step_data(
          'scan for license errors.run licenses tool',
          stdout=api.raw_io.output_text(
              'debug info that is not a license error\n')),
      api.post_process(post_process.StepTextEquals,
                       'scan for license errors.summary of results',
                       'Found 0 README files with license errors.'),
      api.post_process(post_process.StepSuccess, 'scan for license errors'),
      api.expect_status('SUCCESS'),
      api.post_process(post_process.DropExpectation),
  )
