# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import base64
import re

from recipe_engine.post_process import (
    DoesNotRun, DropExpectation, MustRun, StepTextEquals)

from RECIPE_MODULES.build.v8.v8version import choose_revision_to_roll

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

V8_VERSION_RE = re.compile(r'^\d+\.\d+\.\d+(?:\.\d+)?$')


def get_v8_revision(api, name, deps):
  deps_file = api.path.mkdtemp(name).join('DEPS')
  api.file.write_text(name, deps_file, deps)
  revision = api.gclient(
      'get %s deps' % name,
      ['getdep', '--var=v8_revision', '--deps-file=%s' % deps_file],
      stdout=api.raw_io.output_text(),
  ).stdout.strip()
  api.step.active_result.presentation.logs['revision'] = [revision]
  return revision


def get_consistent_v8_revisions(api):
  """Returns the V8 revisions from gitiles and the local file."""
  # Get deps file from gitiles.
  gitiles_deps = api.gitiles.download_file(
      'https://chromium.googlesource.com/chromium/src',
      'DEPS',
      branch='refs/heads/main',
      step_test_data=lambda: api.gitiles.test_api.make_encoded_file(
          TEST_DEPS_FILE % 'deadbeef'),
  )

  # Get the deps file used by the auto roller.
  local_deps = api.v8.git_output(
      'cat-file', 'blob', 'HEAD:DEPS',
      step_test_data=lambda: api.raw_io.test_api.stream_output_text(
          TEST_DEPS_FILE % 'deadbeef'),
  )

  return (get_v8_revision(api, 'gitiles', gitiles_deps),
          get_v8_revision(api, 'local', local_deps))


def get_v8_tag(api, revision):
  """Returns the V8 version tag associated with a revision or None."""
  tags = api.v8.git_output('tag', '--points-at', revision).split('\n')
  return next((tag for tag in tags if V8_VERSION_RE.match(tag)), None)


def get_next_v8_revision(api, last_v8_revision):
  """Choose the next newest viable V8 revision to roll.

  Args:
    last_v8_revision: The previously rolled revision.
  """
  with api.step.nest('Choose revision') as parent:
    with api.context(cwd=api.v8.checkout_root.join('v8')):
      api.git('fetch', 'origin', '+refs/tags/*:refs/tags/*')

      last_version = get_v8_tag(api, last_v8_revision)
      assert last_version, 'The last rolled v8 revision is not tagged.'

      ref_lines = api.v8.git_output(
          'for-each-ref', '--count=160', '--sort=-committerdate',
          '--format', '%(refname) %(objectname)  %(committerdate)',
          'refs/tags/*',
      ).split('\n')
      revision, reason = choose_revision_to_roll(ref_lines, last_version)
      parent.presentation.step_text = reason
      return revision


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

  last_v8_revision, last_v8_revision_local = get_consistent_v8_revisions(api)

  # Require local and gitiles DEPS to be consistent before proceeding.
  if last_v8_revision != last_v8_revision_local:
    api.step('Local checkout is lagging behind.', cmd=None)
    api.step.active_result.presentation.status = api.step.WARNING
    return

  with api.context(cwd=api.path['checkout'].join('v8'),
                   env={'DEPOT_TOOLS_UPDATE': '0'},
                   env_prefixes={'PATH': [api.v8.depot_tools_path]}):
    next_v8_revision = get_next_v8_revision(api, last_v8_revision)
    if not next_v8_revision:
      return

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
           '--last-roll', last_v8_revision,
           '--revision', next_v8_revision,
           '--work-dir', api.path['cache'].join(safe_buildername, 'workdir')],
      )


TEST_REF_DATA = """
refs/tags/11.7.10-pgo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2 Fri Jul 7 11:32:02 2023 +0000
refs/tags/11.7.10 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2 Fri Jul 7 11:32:02 2023 +0000
refs/tags/11.7.9-pgo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1 Fri Jul 7 10:32:02 2023 +0000
refs/tags/11.7.9 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1 Fri Jul 7 10:32:02 2023 +0000
"""


def GenTests(api):
  def gerrit_changes(changes, second=False):
    return api.override_step_data(
        'gerrit changes' + (' (2)' if second else ''),
        api.json.output(changes))

  def last_v8_revision():
    def deps(name):
      return api.override_step_data(
          f'gclient get {name} deps',
          api.raw_io.stream_output_text('deadbeef', stream='stdout'))
    return deps('gitiles') + deps('local')

  def v8_tag(tag):
    return api.override_step_data(
        'Choose revision.git tag',
        api.raw_io.stream_output_text(f'{tag}-pgo\n{tag}', stream='stdout'))

  def v8_ref_data():
    return api.override_step_data(
        'Choose revision.git for-each-ref',
        api.raw_io.stream_output_text(TEST_REF_DATA, stream='stdout'))

  yield api.test(
      'standard',
      gerrit_changes([]),
      last_v8_revision(),
      v8_tag('11.7.8'),
      v8_ref_data(),
      api.post_process(
          StepTextEquals,
          'Choose revision',
          'found revision to roll: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2'),
      status='SUCCESS',
  )
  yield api.test(
      'nothing_new',
      gerrit_changes([]),
      last_v8_revision(),
      v8_tag('11.7.11'),
      v8_ref_data(),
      api.post_process(
          StepTextEquals,
          'Choose revision',
          'found no newer revision than: 11.7.11'),
      api.post_process(DoesNotRun, 'roll deps'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )
  yield api.test(
      'rolling_deactivated',
      api.url.text('check roll status', '0'),
      status='SUCCESS',
  )
  yield api.test(
      'active_roll',
      gerrit_changes([{'_number': '123'}]),
      gerrit_changes([{'_number': '123'}], second=True),
      status='SUCCESS',
  )
  yield api.test(
      'stale_roll',
      gerrit_changes([{'_number': '123'}]),
      gerrit_changes([], second=True),
      status='SUCCESS',
  )
  yield api.test(
      'inconsistent_state',
      gerrit_changes([]),
      last_v8_revision(),
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
      gerrit_changes([]),
      last_v8_revision(),
      api.runtime(is_experimental=True),
      v8_tag('11.7.8'),
      v8_ref_data(),
      api.post_process(MustRun, 'fake roll deps'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )
  yield api.test(
      'stale_roll_experimental',
      gerrit_changes([{'_number': '123'}]),
      gerrit_changes([], second=True),
      api.runtime(is_experimental=True),
      api.post_process(MustRun, 'fake resubmit to CQ'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )
