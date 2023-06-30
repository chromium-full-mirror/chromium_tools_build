# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import base64

DEPS = [
  'chromium',
  'depot_tools/bot_update',
  'depot_tools/gclient',
  'depot_tools/gerrit',
  'depot_tools/git',
  'depot_tools/gitiles',
  'recipe_engine/buildbucket',
  'recipe_engine/context',
  'recipe_engine/file',
  'recipe_engine/json',
  'recipe_engine/path',
  'recipe_engine/properties',
  'recipe_engine/raw_io',
  'recipe_engine/runtime',
  'recipe_engine/service_account',
  'recipe_engine/step',
  'recipe_engine/url',
  'v8',
]

TEST_DEPS_FILE = """
vars = {
  'chromium_git': 'https://chromium.googlesource.com',
  'v8_revision': '%s',
}

deps = {
  'src/v8':
    Var('chromium_git') + '/v8/v8.git' + '@' +  Var('v8_revision'),
}
"""


def get_v8_revision(api, name, deps):
  deps_file = api.path.mkdtemp(name).join('DEPS')
  api.file.write_text(name, deps_file, deps)
  return api.gclient(
      'get %s deps' % name,
      ['getdep', '--var=v8_revision', '--deps-file=%s' % deps_file],
      stdout=api.raw_io.output_text(),
  ).stdout.strip()


def is_gitiles_inconsistent(api):
  """Returns whether the DEPS from gitiles and the local file are inconsistent.
  """
  # Get deps file from gitiles.
  gitiles_deps = api.gitiles.download_file(
      'https://chromium.googlesource.com/chromium/src',
      'DEPS',
      branch='refs/heads/main',
      step_test_data=lambda: api.gitiles.test_api.make_encoded_file(
          TEST_DEPS_FILE % 'deadbeef'),
  )

  # Get the deps file used by the auto roller.
  local_deps = api.git(
      'cat-file', 'blob', 'HEAD:DEPS',
      stdout=api.raw_io.output_text(),
      step_test_data= lambda: api.raw_io.test_api.stream_output_text(
          TEST_DEPS_FILE % 'deadbeef'),
  ).stdout

  return (get_v8_revision(api, 'gitiles', gitiles_deps) !=
          get_v8_revision(api, 'local', local_deps))


def RunSteps(api):
  api.gclient.set_config('chromium')
  api.gclient.apply_config('v8_tot')

  # We need a full V8 checkout as well in order to checkout V8 DEPS, which
  # includes pinned depot_tools used by release scripts that we invoke below.
  api.gclient.apply_config('v8_bare')

  output = api.url.get_text(
      'https://v8-roll.appspot.com/status',
      step_name='check roll status',
      default_test_data='1',
  ).output
  api.step.active_result.presentation.logs['output'] = output.splitlines()
  if output.strip() != '1':
    api.step.active_result.presentation.step_text = 'Rolling deactivated'
    return

  api.step.active_result.presentation.step_text = 'Rolling activated'

  # Check for an open auto-roller CL. There should be at most one CL in the
  # chromium project, which is the last roll.
  push_account = (
      # TODO(sergiyb): Replace with api.service_account.default().get_email()
      # when https://crbug.com/846923 is resolved.
      'v8-ci-autoroll-builder@chops-service-accounts.iam.gserviceaccount.com')
  commits = api.gerrit.get_changes(
      'https://chromium-review.googlesource.com',
    query_params=[
      ('project', 'chromium/src'),
      ('owner', push_account),
      ('status', 'open'),
    ],
    limit=1,
  )

  if commits:
    cq_commits = api.gerrit.get_changes(
      'https://chromium-review.googlesource.com/a',
      query_params=[
        ('change', commits[0]['_number']),
        ('label', 'Commit-Queue>=1'),
      ],
      limit=1,
    )

    if not cq_commits:
      api.v8.checkout()
      with api.context(
          cwd=api.path['checkout'],
          env_prefixes={'PATH': [api.v8.depot_tools_path]}):
        if api.runtime.is_experimental:
          api.step('fake resubmit to CQ', cmd=None)
        else:
          api.git('cl', 'set-commit', '-i', commits[0]['_number'])
        api.step.active_result.presentation.step_text = (
          'Stale roll found. Resubmitted to CQ.')
    else:
      assert cq_commits[0]['_number'] == commits[0]['_number']
      api.step.active_result.presentation.step_text = 'Active rolls found.'

    return

  # Make it more likely to avoid inconsistencies when hitting different
  # mirrors.
  api.v8.python(
      'wait for consistency',
      api.v8.resource('sleep_20_seconds.py'),
  )

  api.v8.checkout()

  # Require local and gitiles DEPS to be consistent before proceeding.
  if is_gitiles_inconsistent(api):
    api.step('Local checkout is lagging behind.', cmd=None)
    api.step.active_result.presentation.status = api.step.WARNING
    return

  with api.context(cwd=api.path['checkout'].join('v8'),
                   env={'DEPOT_TOOLS_UPDATE': '0'},
                   env_prefixes={'PATH': [api.v8.depot_tools_path]}):
    safe_buildername = ''.join(
      c if c.isalnum() else '_' for c in api.buildbucket.builder_name)
    if api.runtime.is_experimental:
      api.step('fake roll deps', cmd=None)
    else:
      api.v8.python(
          'roll deps',
          api.v8.checkout_root.join(
              'v8', 'tools', 'release', 'auto_roll.py'),
          ['--chromium', api.path['checkout'],
           '--author', push_account,
           '--reviewer', 'hablich@chromium.org,'
                         'vahl@chromium.org,'
                         'v8-waterfall-sheriff@grotations.appspotmail.com',
           '--roll',
           '--work-dir', api.path['cache'].join(safe_buildername, 'workdir')],
      )


def GenTests(api):
  yield api.test(
      'standard',
      api.override_step_data('gerrit changes', api.json.output([])),
      status='SUCCESS',
  )
  yield api.test(
      'rolling_deactivated',
      api.url.text('check roll status', '0'),
      status='SUCCESS',
  )
  yield api.test(
      'active_roll',
      api.override_step_data('gerrit changes',
                             api.json.output([{
                                 '_number': '123'
                             }])),
      api.override_step_data('gerrit changes (2)',
                             api.json.output([{
                                 '_number': '123'
                             }])),
      status='SUCCESS',
  )
  yield api.test(
      'stale_roll',
      api.override_step_data('gerrit changes',
                             api.json.output([{
                                 '_number': '123'
                             }])),
      api.override_step_data('gerrit changes (2)', api.json.output([])),
      status='SUCCESS',
  )
  yield api.test(
      'inconsistent_state',
      api.override_step_data('gerrit changes', api.json.output([])),
      api.override_step_data(
          'git cat-file',
          api.raw_io.stream_output_text(TEST_DEPS_FILE % 'beefdead')),
      api.override_step_data(
          'gclient get local deps',
          api.raw_io.stream_output_text('beefdead', stream='stdout'),
      ),
      status='SUCCESS',
  )
  yield api.test(
      'standard_experimental',
      api.override_step_data('gerrit changes', api.json.output([])),
      api.runtime(is_experimental=True),
      status='SUCCESS',
  )
  yield api.test(
      'stale_roll_experimental',
      api.override_step_data('gerrit changes',
                             api.json.output([{
                                 '_number': '123'
                             }])),
      api.override_step_data('gerrit changes (2)', api.json.output([])),
      api.runtime(is_experimental=True),
      status='SUCCESS',
  )
