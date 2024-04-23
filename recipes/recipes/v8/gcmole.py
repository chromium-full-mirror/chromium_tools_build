# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'depot_tools/git',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'v8',
]

GS_BUCKET = 'chrome-v8-gcmole'


def RunSteps(api):
  api.gclient.set_config('v8')
  api.chromium.set_config('v8')
  api.v8.checkout()
  api.v8.runhooks()

  depot_tools_path = api.path.checkout_dir.joinpath('third_party',
                                                    'depot_tools')
  with api.context(env_prefixes={'PATH': [depot_tools_path]}):
    api.git('branch', '-D', 'gcmole_update', ok_ret='any')
    api.git('clean', '-ffd')
    api.git('new-branch', 'gcmole_update')

    gcmole_root = api.path.checkout_dir.joinpath('tools', 'gcmole')
    api.step('Build gcmole', [gcmole_root / 'bootstrap.sh'])
    api.step('Package gcmole', [gcmole_root / 'package.sh'])

    api.v8.python(
        'upload_to_google_storage',
        api.depot_tools.upload_to_google_storage_path,
        ['-b', GS_BUCKET, gcmole_root / 'gcmole-tools.tar.gz'],
    )

    changes = api.git(
        'status', '--porcelain',
        stdout=api.raw_io.output_text()).stdout.strip()
    if changes:
      api.git('commit', '-am', '[tools] Update gcmole')
      api.git(
          'cl',
          'upload',
          '-f',
          '-d',
          '--bypass-hooks',
          '--send-mail',
          '--r-owners',
      )


def GenTests(api):
  yield api.test(
      "default test",
      api.override_step_data(
          'git status',
          api.raw_io.stream_output_text('some change', stream='stdout'),
      ),
      api.post_process(post_process.MustRun, 'git commit', 'git cl'),
      api.post_process(
          post_process.Filter('Build gcmole', 'Package gcmole',
                              'upload_to_google_storage', 'git cl')),
      status='SUCCESS',
  )

  yield api.test(
      "no change test",
      api.override_step_data(
          'git status',
          api.raw_io.stream_output_text('', stream='stdout'),
      ),
      api.post_process(post_process.DoesNotRun, 'git commit', 'git cl'),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )
