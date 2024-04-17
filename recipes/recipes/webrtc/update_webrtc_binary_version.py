# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium_checkout',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/gerrit',
    'depot_tools/git',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/step',
    'webrtc',
]

GERRIT_URL = 'https://webrtc-review.googlesource.com'
GERRIT_PROJECT = 'src'


def RunSteps(api):
  api.gclient.set_config('webrtc')
  api.gclient.c.target_os.add('linux')
  api.chromium_checkout.ensure_checkout()

  with api.context(cwd=api.path.checkout_dir):
    # Check for an open CL.
    commits = api.gerrit.get_changes(
        GERRIT_URL,
        query_params=[
            ('project', GERRIT_PROJECT),
            ('owner', 'self'),
            ('status', 'open'),
        ],
        limit=1,
    )
    if commits:
      cq_commits = api.gerrit.get_changes(
          GERRIT_URL,
          query_params=[
              ('change', commits[0]['_number']),
              ('label', 'Commit-Queue>=1'),
          ],
          limit=1,
      )
      if cq_commits:
        assert cq_commits[0]['_number'] == commits[0]['_number']
        api.step.active_result.presentation.step_text = 'Active CL found.'
        return

      with api.context(env={'SKIP_GCE_AUTH_FOR_GIT': '1'}):
        with api.depot_tools.on_path():
          api.git('cl', 'set-close', '-i', commits[0]['_number'])
        api.step.active_result.presentation.step_text = (
            'Stale CL found. Abandoned.')

    # Enforce a clean state, and discard any local commits from previous runs.
    api.git('checkout', '-f', 'main')
    api.git('pull', 'origin', 'main')
    api.git('clean', '-ffd')

    # Run the update script. It will take care of branch creation, WebRTC
    # version update, uploading etc. It will also delete any previous version
    # update branch.
    script_path = api.path.checkout_dir.join('tools_webrtc', 'version_updater',
                                             'update_version.py')

    params = ['--clean']
    cmd = ['vpython3', '-u', script_path] + params
    with api.depot_tools.on_path():
      api.step('Update WebRTC version', cmd)


def GenTests(api):
  base = api.buildbucket.generic_build()

  yield api.test(
      'stale_update',
      base,
      api.override_step_data('gerrit changes',
                             api.json.output([{
                                 '_number': '123'
                             }])),
      api.override_step_data('gerrit changes (2)', api.json.output([])),
  )
  yield api.test(
      'previous_update_in_cq',
      base,
      api.override_step_data('gerrit changes',
                             api.json.output([{
                                 '_number': '123'
                             }])),
      api.override_step_data('gerrit changes (2)',
                             api.json.output([{
                                 '_number': '123'
                             }])),
  )
