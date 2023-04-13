# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for checking that V8 is rolled in Chromium in a timely manner.
"""

from datetime import datetime

from recipe_engine.recipe_api import Property
from recipe_engine.post_process import (DropExpectation, StepFailure,
                                        ResultReason)

DEPS = [
    'chromiumdash',
    'depot_tools/gitiles',
    'depot_tools/gsutil',
    'depot_tools/gclient',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/time',
    'recipe_engine/url',
]

PROPERTIES = {
    'max_gap_seconds':
        Property(
            help='Maximum allowed time difference between a revision landing'
            ' in V8 and rolling it into Chromium.',
            default=60 * 60 * 24 * 7,
            kind=int)
}


def RunSteps(api, max_gap_seconds):
  branches = api.chromiumdash.milestones(0, only_active=True)
  if not branches:
    raise api.step.StepFailure('No branches found')

  branches_in_sync = all( # pylint: disable=use-a-generator
      [check_branch(api, branch, max_gap_seconds) for branch in branches])
  if not branches_in_sync:
    raise api.step.StepFailure('Some branches are falling behind')


def check_branch(api, branch, max_gap_seconds):
  chromium_branch = branch['chromium_branch']
  assert branch['v8_branch'].endswith('-lkgr')
  v8_branch = branch['v8_branch'][:-len('-lkgr')]

  with api.step.nest(f'branch {v8_branch} ({chromium_branch})'):
    deps_file = download_chromium_deps(api, chromium_branch)
    last_rolled_revision = read_last_rolled_revision(api, deps_file)
    commits_not_rolled = get_commits_not_rolled(api, last_rolled_revision,
                                                v8_branch)
    return all([ # pylint: disable=use-a-generator
        check_commit(api, commit, max_gap_seconds)
        for commit in commits_not_rolled
    ])


def download_chromium_deps(api, chromium_branch):
  chromium_deps = api.gitiles.download_file(
      'https://chromium.googlesource.com/chromium/src',
      'DEPS',
      branch='refs/branch-heads/%s' % chromium_branch,
  )
  deps_file = api.path.mkdtemp(f'chromium{chromium_branch}').join('DEPS')
  api.file.write_text(f'chromium/{chromium_branch} DEPS', deps_file,
                      chromium_deps)
  return deps_file


def read_last_rolled_revision(api, deps_file):
  return api.gclient(
      'get v8_revision',
      ['getdep', '--var=v8_revision', f'--deps-file={deps_file}'],
      stdout=api.raw_io.output_text(add_output_log=True),
  ).stdout.strip()


def get_commits_not_rolled(api, last_rolled_revision, v8_branch):
  commits, _ = api.gitiles.log(
      url='https://chromium.googlesource.com/v8/v8',
      ref=f'{last_rolled_revision}..refs/branch-heads/{v8_branch}',
      limit=100,
      step_name='Get roll gap',
  )
  return commits


def check_commit(api, commit, max_gap_seconds):
  commit_time = datetime.strptime(commit['committer']['time'],
                                  '%a %b %d %H:%M:%S %Y')
  time_gap = api.time.utcnow() - commit_time

  step_result = api.step(f'checking revision {commit["commit"]}', [])
  step_result.presentation.logs['rev time'] = str(commit_time)
  step_result.presentation.logs['time gap'] = str(time_gap)

  if time_gap.total_seconds() > max_gap_seconds:
    step_result = api.step(
        f'Revision {commit["commit"]} was not rolled for '
        f'{time_gap}', [])
    step_result.presentation.status = api.step.FAILURE
    return False
  return True


def GenTests(api):

  def fake_branches():
    return api.url.json(
        'GET https://chromiumdash.appspot.com/fetch_milestones?'
        'num=0&only_active=true', [{
            'chromium_branch': '5555',
            'v8_branch': '10.3-lkgr'
        }, {
            'chromium_branch': '6666',
            'v8_branch': '11.4-lkgr'
        }])

  def fake_commit():
    return api.json.output({
        'log': [{
            'commit': 'deadbeef',
            'committer': {
                'time': 'Tue Apr 10 10:11:39 2023'
            },
        },],
    })

  apr_10_2023_09 = 1681110000

  yield api.test(
      "no branches",
      api.url.json(
          'GET https://chromiumdash.appspot.com/fetch_milestones?'
          'num=0&only_active=true', {}),
      api.expect_status('FAILURE'),
      api.post_process(ResultReason, "No branches found"),
      api.post_process(DropExpectation),
  )

  yield api.test(
      "branches in sync",
      fake_branches(),
      api.step_data(
          'branch 10.3 (5555).fetch refs/branch-heads/5555:DEPS',
          api.gitiles.make_encoded_file('DEPS'),
      ),
      api.step_data(
          'branch 11.4 (6666).fetch refs/branch-heads/6666:DEPS',
          api.gitiles.make_encoded_file('DEPS'),
      ),
      api.step_data('branch 10.3 (5555).Get roll gap', fake_commit()),
      api.step_data('branch 11.4 (6666).Get roll gap', fake_commit()),
      api.time.seed(apr_10_2023_09),
      api.expect_status('SUCCESS'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      "branches out of sync",
      fake_branches(),
      api.step_data(
          'branch 10.3 (5555).fetch refs/branch-heads/5555:DEPS',
          api.gitiles.make_encoded_file('DEPS'),
      ),
      api.step_data(
          'branch 11.4 (6666).fetch refs/branch-heads/6666:DEPS',
          api.gitiles.make_encoded_file('DEPS'),
      ),
      api.step_data('branch 10.3 (5555).Get roll gap', fake_commit()),
      api.step_data('branch 11.4 (6666).Get roll gap', fake_commit()),
      api.post_process(
          StepFailure,
          "branch 11.4 (6666).Revision deadbeef was not rolled for 7 days,"
          " 20:48:24"),
      api.time.seed(apr_10_2023_09 + 60 * 60 * 24 * 8),
      api.expect_status('FAILURE'),
      api.post_process(ResultReason, "Some branches are falling behind"),
      api.post_process(DropExpectation),
  )
