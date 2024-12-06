# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Set the release branch in refs/heads/<channel> to the most recent version
from chromiumdash."""

import datetime as dt

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.v8.release_branch_updater import InputProperties

from recipe_engine.recipe_api import Property
from recipe_engine import post_process as post

DEPS = [
    'chromiumdash',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/gitiles',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/time',
    'recipe_engine/url',
    'v8',
]

PROPERTIES = InputProperties

DEFAULT_MAX_AGE_WEEKS = 5

TIME_FORMAT = '%a %b %d %H:%M:%S %Y'
V8_REPO = 'https://chromium.googlesource.com/v8/v8/'


def RunSteps(api, props):
  updater = ReleaseBranchUpdater(api, props.channels)
  updater.init()
  channels = updater.retrieve_updates()
  updater.update_channels(channels)

  errors = updater.validate_channels()
  if errors:
    note = '\n'.join(errors)
    return result_pb.RawResult(status=common_pb.FAILURE, summary_markdown=note)


class Channel:

  def __init__(self, api, milestones, spec):
    self.api = api
    self.milestones = milestones
    self.spec = spec

    self._current_head = self._fetch_current_head()
    self._next_head = self._fetch_next_head()

  @property
  def refname(self):
    return self.spec.refname

  @property
  def source_channel(self):
    return self.spec.source_channel

  @property
  def max_age_sec(self):
    weeks = self.spec.max_age_weeks or DEFAULT_MAX_AGE_WEEKS
    return int(weeks * 7 * 24 * 60 * 60)

  @property
  def current_head(self):
    return self._current_head

  @property
  def next_head(self):
    return self._next_head

  def update_head(self):
    with self.api.step.nest(f'Update channel {self.refname}'):
      self.api.gclient.set_config('v8')
      self.api.v8.checkout(self.next_head)
      self._git('push', 'origin', f'{self.next_head}:refs/heads/{self.refname}',
                '-f')

  def _git(self, *cmd):
    cwd = self.api.v8.checkout_root / 'v8'
    with self.api.context(cwd=cwd):
      return self.api.v8.git_output(*cmd)

  def validate_age(self):
    with self.api.step.nest(f'Check {self.refname} age') as p:
      age = self._get_age_sec()
      p.step_text = f'Current: {age}s · Max: {self.max_age_sec}s'

      if age > self.max_age_sec:
        return [f'{self.refname} is outdated: {age}s > {self.max_age_sec}s.']

      return []

  def _get_age_sec(self):
    head = self.next_head or self.current_head
    log = self.api.gitiles.commit_log(V8_REPO.rstrip('/'), head, attempts=5)
    commit_time = dt.datetime.strptime(log['committer']['time'], TIME_FORMAT)
    return int((self.api.time.utcnow() - commit_time).total_seconds())

  def _fetch_current_head(self):
    return self.api.git.ls_remote(
        V8_REPO,
        f'refs/heads/{self.refname}',
        name=f'git: Fetch current head for refs/heads/{self.refname}')

  def _fetch_next_head(self):
    milestones = [
        m for m in self.milestones if m['schedule_phase'] == self.source_channel
    ]
    if not milestones:
      return None

    milestone = sorted(milestones, key=lambda ms: -ms['milestone'])[0]
    chromium_branch = milestone['chromium_branch']
    return self.api.git.ls_remote(
        V8_REPO,
        f'refs/heads/chromium/{chromium_branch}',
        name=f'git: Fetch next head for refs/heads/{self.refname}')


class ReleaseBranchUpdater:

  def __init__(self, api, channel_specs):
    self.channels = []
    self.channel_specs = channel_specs
    self.api = api
    self.milestones = []

  def init(self):
    with self.api.step.nest('Initialize') as p:
      self._ensure_checkout()
      self._fetch_milestones()
      p.logs['milestones'] = self.api.json.dumps(self.milestones)
      self._create_channels()

  def _ensure_checkout(self):
    self.api.file.ensure_directory('ensure v8 checkout dir',
                                   self.api.v8.checkout_root / 'v8')

  def _fetch_milestones(self):
    self.milestones = self.api.chromiumdash.milestones(
        100, 'chromiumdash: Fetch recent milestones', only_active=True)

  def _create_channels(self):
    for spec in self.channel_specs:
      self.channels.append(Channel(self.api, self.milestones, spec))

  def retrieve_updates(self):
    updates = []
    for channel in self.channels:
      with self.api.step.nest(f'Verify {channel.refname}') as p:
        if channel.next_head is None:
          p.step_text = 'Channel is unavailable at chromiumdash.'
          continue

        if channel.next_head == channel.current_head:
          p.step_text = f'{channel.next_head} is the current head.'
          continue

        p.step_text = f'Update head to {channel.next_head}.'
        updates.append(channel)

    return updates

  def update_channels(self, channels):
    with self.api.step.nest(f'Update {len(channels)} channel(s)'):
      if not channels:
        return

      for channel in channels:
        channel.update_head()

  def validate_channels(self):
    with self.api.step.nest('Check freshness'):
      outdated = []
      for channel in self.channels:
        outdated += channel.validate_age()

    return outdated


def GenTests(api):

  def milestone(milestone_number, chromium_branch, channel='stable'):
    return {
        "chromium_branch": chromium_branch,
        "schedule_phase": channel,
        "milestone": milestone_number,
    }

  def ls_remote(step_name, branch, revision):
    return api.override_step_data(
        step_name,
        api.raw_io.stream_output_text(
            f'{revision}\t{branch}\n', stream='stdout'))

  def test(name, config, *args):
    stable_revision = config.get('stable_revision', '7ea')
    channels = config.get('channels', [
        {
            "refname": "stable",
            "source_channel": "stable",
            "max_age_weeks": 5
        },
    ])
    now = config.get('now', 1729071780)  # 2024-10-16T09:43:00+00

    channel_mocks = []
    for channel in channels:
      refname = channel['refname']
      channel_mocks.append(
          ls_remote(
              f'Initialize.git: Fetch current head for refs/heads/{refname}',
              f'refs/heads/{refname}', 'c0ffee'))

      revision = 'c0ffee'
      if refname == 'stable':
        revision = stable_revision

      if refname in {'stable', 'beta'}:
        channel_mocks.append(
            ls_remote(
                f'Initialize.git: Fetch next head for refs/heads/{refname}',
                'refs/heads/chromium/129', revision))

      channel_mocks.append(
          api.step_data(
              f'Check freshness.Check {refname} age.commit log: {revision}',
              api.json.output(
                  {'committer': {
                      'time': 'Wed Oct 16 09:42:00 2024'
                  }}),
          ))

    return api.test(
        name,
        api.properties(channels=channels),
        api.time.seed(now),
        api.time.step(0),
        api.url.json('Initialize.chromiumdash: Fetch recent milestones', [
            milestone(129, '6668'),
            milestone(128, '6613'),
            milestone(130, '6723', channel='beta'),
        ]),
        *channel_mocks,
        *args,
        api.post_process(post.DropExpectation),
    )

  yield test(
      'new-revision',
      {},
      api.post_process(post.StepTextEquals, 'Verify stable',
                       'Update head to 7ea.'),
      api.post_process(post.MustRun,
                       'Update 1 channel(s).Update channel stable'),
  )

  yield test(
      'no-updates',
      {'stable_revision': 'c0ffee'},
      api.post_process(post.StepTextEquals, 'Verify stable',
                       'c0ffee is the current head.'),
      api.post_process(post.MustRun, 'Update 0 channel(s)'),
  )

  yield test(
      'missing-chromiumdash-channel',
      {
          'channels': [
              {
                  "refname": "stable",
                  "source_channel": "stable",
                  "max_age_weeks": 5
              },
              {
                  "refname": "dev",
                  "source_channel": "dev"
              },
          ],
      },
      api.post_process(post.MustRun,
                       'Update 1 channel(s).Update channel stable'),
      api.post_process(post.StepTextEquals, 'Verify dev',
                       'Channel is unavailable at chromiumdash.'),
  )

  yield test(
      'validate-channels',
      {},
      api.post_process(
          post.StepTextEquals,
          'Check freshness.Check stable age',
          'Current: 60s · Max: 3024000s',
      ),
  )

  yield test(
      'validate-channels-failure',
      {"now": 1734345780},  # 2024-12-16T09:43:00+00
      api.post_process(
          post.StepTextEquals,
          'Check freshness.Check stable age',
          'Current: 5274060s · Max: 3024000s',
      ),
      api.expect_status('FAILURE'),
      api.post_process(post.SummaryMarkdown,
                       'stable is outdated: 5274060s > 3024000s.'),
  )
