# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for checking that V8 is rolled in Chromium in a timely manner.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from recipe_engine.recipe_api import Property
from recipe_engine.post_process import (DropExpectation, StepFailure,
                                        SummaryMarkdown)
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine.result import RawResult

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
    'v8',
]

PROPERTIES = {
    'max_gap_seconds':
        Property(
            help='Maximum allowed time difference between a revision landing'
            ' in V8 and rolling it into Chromium.',
            default=60 * 60 * 12,
            kind=int),
    'branch_cut_max_gap_seconds':
        Property(
            help='Overrides the max_gap_seconds on branch cut days.',
            default=60 * 60 * 36,
            kind=int)
}


@dataclass
class CommitTime:
  revision: str
  commit_time: datetime
  time_gap: timedelta

  def __str__(self):
    return f'{self.revision[:8]} {self.time_gap_hours}h'

  def overdue_message(self):
    return f'Revision {self.revision} was not rolled for {self.formatted_time_gap}'

  def is_overdue(self, max_gap_seconds):
    return self.time_gap.total_seconds() > max_gap_seconds

  @property
  def formatted_time_gap(self):
    return str(self.time_gap).split(".", maxsplit=1)[0]

  @property
  def time_gap_hours(self):
    return self.time_gap.total_seconds() // 3600


@dataclass
class BranchResult:
  chromium_branch: str
  v8_branch: str
  commits_not_rolled: int
  overdue_commits: list[CommitTime]

  def summary(self):
    if self.is_overdue:
      return f'{self.num_overdue_commits} overdue revs in {self.v8_branch}'
    if self.commits_not_rolled:
      return f'{self.commits_not_rolled} revs not rolled in {self.v8_branch}'
    return None

  @property
  def num_overdue_commits(self):
    return len(self.overdue_commits)

  @property
  def is_overdue(self):
    return self.num_overdue_commits > 0


def RunSteps(api, max_gap_seconds, branch_cut_max_gap_seconds):
  now = api.time.utcnow()
  branches = api.chromiumdash.milestones(0, only_active=True)
  if not branches:
    raise api.step.StepFailure('No branches found')

  branch_results = [
      check_branch(api, branch, max_gap_seconds, now) for branch in branches
  ]

  adjust_for_branch_cut_day(api, branch_results, branch_cut_max_gap_seconds)

  overdue_branches = [br for br in branch_results if br.is_overdue]

  status = common_pb.FAILURE if overdue_branches else common_pb.SUCCESS
  return RawResult(
      status=status,
      summary_markdown='; '.join(
          b.summary() for b in branch_results if b.summary()),
  )


def check_branch(api, branch, max_gap_seconds, now):
  chromium_branch = branch['chromium_branch']
  v8_branch = branch['v8_branch']

  with api.step.nest(f'branch {v8_branch} ({chromium_branch})') as step:
    deps_file = download_chromium_deps(api, chromium_branch)
    last_rolled_revision = read_last_rolled_revision(api, deps_file)
    commits_not_rolled = get_commits_not_rolled(api, last_rolled_revision,
                                                v8_branch)
    commit_times = [commit_time(commit, now) for commit in commits_not_rolled]
    step.step_text = '; '.join(str(c) for c in commit_times)

    overdue_commits = [
        ct for ct in commit_times if ct.is_overdue(max_gap_seconds)
    ]

    for ct in overdue_commits:
      step_result = api.step(ct.overdue_message(), [])
      step_result.presentation.status = api.step.FAILURE

    return BranchResult(chromium_branch, v8_branch, len(commits_not_rolled),
                        overdue_commits)


def adjust_for_branch_cut_day(api, branch_results, branch_cut_max_gap_seconds):
  latest_branch = max(branch_results, key=lambda br: int(br.chromium_branch))
  if not latest_branch.overdue_commits:
    return
  with api.step.nest('branch cut day check'):
    cronologicaly_first_index = -1
    first_commit = latest_branch.overdue_commits[cronologicaly_first_index]
    api.step(f'Checking first commit in range ({first_commit.revision})', [])
    version = read_v8_version(api, first_commit.revision)
    within_bounds = not first_commit.is_overdue(branch_cut_max_gap_seconds)
    if version.patch == '1' and within_bounds:
      api.step('Brach cut day! Using the extended gap.', [])
      latest_branch.overdue_commits = []


def read_v8_version(api, v8_revision):
  version_blob = api.gitiles.download_file(
      'https://chromium.googlesource.com/v8/v8',
      'include/v8-version.h',
      branch=v8_revision,
  )
  return api.v8.version_from_file(version_blob)


def download_chromium_deps(api, chromium_branch):
  chromium_deps = api.gitiles.download_file(
      'https://chromium.googlesource.com/chromium/src',
      'DEPS',
      branch='refs/branch-heads/%s' % chromium_branch,
  )
  deps_file = api.path.mkdtemp(f'chromium{chromium_branch}') / 'DEPS'
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


def commit_time(commit, now):
  committer_time = datetime.strptime(commit['committer']['time'],
                                  '%a %b %d %H:%M:%S %Y')
  time_gap = now - committer_time
  return CommitTime(commit['commit'], committer_time, time_gap)


def GenTests(api):

  def fake_branches():
    return api.url.json(
        'GET https://chromiumdash.appspot.com/fetch_milestones?'
        'num=0&only_active=true', [{
            'chromium_branch': '5555',
            'v8_branch': '10.3'
        }, {
            'chromium_branch': '6666',
            'v8_branch': '11.4'
        }])

  def fake_commit():
    return api.json.output({
        'log': [{
            'commit': 'deadbeef',
            'committer': {
                'time': 'Tue Apr 10 00:00:00 2023'
            },
        },],
    })

  def fake_version_file(patch='1'):
    return api.step_data(
        'branch cut day check.fetch deadbeef:include/v8-version.h',
        api.gitiles.make_encoded_file('#define V8_MAJOR_VERSION 12\n'
                                      '#define V8_MINOR_VERSION 4\n'
                                      '#define V8_BUILD_NUMBER 254\n'
                                      '#define V8_PATCH_LEVEL %s\n' % patch),
    )

  def no_commits():
    return api.json.output({
        'log': [],
    })

  apr_10_2023_09 = 1681110000

  yield api.test(
      "no branches",
      api.url.json(
          'GET https://chromiumdash.appspot.com/fetch_milestones?'
          'num=0&only_active=true', {}),
      api.post_process(SummaryMarkdown, "No branches found"),
      api.post_process(DropExpectation),
      status='FAILURE',
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
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      "branches in sync - no commits",
      fake_branches(),
      api.step_data(
          'branch 10.3 (5555).fetch refs/branch-heads/5555:DEPS',
          api.gitiles.make_encoded_file('DEPS'),
      ),
      api.step_data(
          'branch 11.4 (6666).fetch refs/branch-heads/6666:DEPS',
          api.gitiles.make_encoded_file('DEPS'),
      ),
      api.step_data('branch 10.3 (5555).Get roll gap', no_commits()),
      api.step_data('branch 11.4 (6666).Get roll gap', no_commits()),
      api.time.seed(apr_10_2023_09),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      "branch cut day branches out of sync",
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
      api.time.seed(apr_10_2023_09 + 60 * 60 * 48),
      fake_version_file(patch='1'),
      api.post_process(
          StepFailure,
          "branch 11.4 (6666).Revision deadbeef was not rolled for 2 days, 7:00:01"
      ),
      api.post_process(SummaryMarkdown,
                       '1 overdue revs in 10.3; 1 overdue revs in 11.4'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      "branch cut day success",
      api.url.json(
          'GET https://chromiumdash.appspot.com/fetch_milestones?'
          'num=0&only_active=true', [{
              'chromium_branch': '6666',
              'v8_branch': '11.4'
          }]),
      api.step_data(
          'branch 11.4 (6666).fetch refs/branch-heads/6666:DEPS',
          api.gitiles.make_encoded_file('DEPS'),
      ),
      api.step_data('branch 11.4 (6666).Get roll gap', fake_commit()),
      api.time.seed(apr_10_2023_09 + 60 * 60 * 20),
      fake_version_file(patch='1'),
      api.post_process(SummaryMarkdown, "1 revs not rolled in 11.4"),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'branch out of sync',
      api.url.json(
          'GET https://chromiumdash.appspot.com/fetch_milestones?'
          'num=0&only_active=true', [{
              'chromium_branch': '6666',
              'v8_branch': '11.4'
          }]),
      api.step_data(
          'branch 11.4 (6666).fetch refs/branch-heads/6666:DEPS',
          api.gitiles.make_encoded_file('DEPS'),
      ),
      api.step_data('branch 11.4 (6666).Get roll gap', fake_commit()),
      api.time.seed(apr_10_2023_09 + 60 * 60 * 20),
      fake_version_file(patch='2'),
      api.post_process(SummaryMarkdown, '1 overdue revs in 11.4'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
