# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


DEPS = [
  'depot_tools/depot_tools',
  'depot_tools/gclient',
  'depot_tools/gerrit',
  'depot_tools/git',
  'libyuv',
  'recipe_engine/context',
  'recipe_engine/json',
  'recipe_engine/path',
  'recipe_engine/runtime',
  'recipe_engine/step',
]


GERRIT_URL = 'https://chromium-review.googlesource.com'
GERRIT_PROJECT = 'libyuv/libyuv'


def RunSteps(api):
  api.gclient.set_config('libyuv')

  # Make sure the checkout contains all deps for all platforms.
  for os in ['linux', 'android', 'mac', 'ios', 'win', 'unix']:
    api.gclient.c.target_os.add(os)

  api.libyuv.checkout()

  with api.context(cwd=api.path.checkout_dir):
    # TODO(oprypin): Replace with api.service_account.default().get_email()
    # when https://crbug.com/846923 is resolved.
    push_account = ('libyuv-ci-autoroll-builder@'
                    'chops-service-accounts.iam.gserviceaccount.com')

    # Check for an open auto-roller CL.
    commits = api.gerrit.get_changes(
        GERRIT_URL,
        query_params=[
            ('project', GERRIT_PROJECT),
            ('owner', push_account),
            ('status', 'open'),
        ],
        limit=1,
    )
    if commits:
      with api.context(env={'SKIP_GCE_AUTH_FOR_GIT': '1'}):
        with api.depot_tools.on_path():
          api.git('cl', 'set-close', '-i', commits[0]['_number'])
        api.step.active_result.presentation.step_text = (
            'Stale roll found. Abandoned.')

    # Enforce a clean state, and discard any local commits from previous runs.
    api.git('checkout', '-f', 'main')
    api.git('pull', 'origin', 'main')
    api.git('clean', '-ffd')

    # Run the roll script. It will take care of branch creation, modifying DEPS,
    # uploading etc. It will also delete any previous roll branch.
    script_path = api.path.checkout_dir.join('tools_libyuv', 'autoroller',
                                             'roll_deps.py')

    params = ['--clean', '--verbose']
    if api.runtime.is_experimental:
      params.append('--skip-cq')
    else:
      params.append('--cq-over=100')

    cmd = ['vpython3', '-u', script_path] + params
    with api.depot_tools.on_path():
      api.step('autoroll DEPS', cmd)


def GenTests(api):
  yield (
      api.test('normal_roll') +
      api.override_step_data('gerrit changes', api.json.output([]))
  )
  yield (api.test('normal_roll_experimental') +
         api.runtime(is_experimental=True))
  yield (
      api.test('stale_roll') +
      api.override_step_data(
          'gerrit changes', api.json.output([{'_number': '123'}]))
  )
