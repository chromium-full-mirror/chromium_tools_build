# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Set the release branch in refs/heads/<channel> to the most recent version
from chromiumdash."""

import datetime as dt

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb

from recipe_engine.recipe_api import Property
from recipe_engine import post_process as post

DEPS = [
    'chromiumdash',
    'depot_tools/gclient',
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

PROPERTIES = {
    # Set the release channels to be updated, e.g. ["stable", "beta"].
    'channels': Property(kind=list, default=None),
}

WEEK = 7 * 24 * 60 * 60

DEFAULT_AGE = 5 * WEEK

# The stable branch is updated at least every 4 weeks, the beta branch every
# week. One week is added to cover delays in the release process.

MAX_CHANNEL_AGE = {
    'stable': 5 * WEEK,
    'beta': 2 * WEEK,
}

TIME_FORMAT = '%a %b %d %H:%M:%S %Y'
V8_REPO = 'https://chromium.googlesource.com/v8/v8/'


def RunSteps(api, channels):
  updater = ReleaseBranchUpdater(api, channels)
  updater.init()
  updates = updater.retrieve_updates()
  updater.update_channels(updates)

  errors = updater.validate_channels()
  if errors:
    note = '\n'.join(errors)
    return result_pb.RawResult(status=common_pb.FAILURE, summary_markdown=note)


class ReleaseBranchUpdater:

  def __init__(self, api, channels):
    self.api = api
    self.channels = channels or []
    self.milestones = []
    self.head_revision_by_channel = {}

  def init(self):
    with self.api.step.nest('Initialize') as p:
      path = self.api.v8.checkout_root / 'v8'
      self.api.file.ensure_directory('ensure v8 checkout dir', path)

      self.milestones = self.api.chromiumdash.milestones(
          100, 'chromiumdash: Fetch recent milestones', only_active=True)

      for channel in self.channels:
        self.head_revision_by_channel[channel] = self._get_ref_head(channel)

      p.logs['milestones'] = self.api.json.dumps(self.milestones)
      p.logs['channels'] = self.api.json.dumps(self.head_revision_by_channel)

  def _get_ref_head(self, channel):
    stdout = self._git(
        'ls-remote',
        V8_REPO,
        f'refs/heads/{channel}',
        name=f'git: Fetch head for refs/heads/{channel}')
    return stdout.split('\t')[0]

  def retrieve_updates(self):
    updates = []
    for channel in self.channels:
      with self.api.step.nest(f'Verify {channel}') as p:
        release = self._get_release(channel)

        if release is None:
          p.step_text = 'Channel is unavailable at chromiumdash.'
          continue

        head = self.head_revision_by_channel[channel]
        if release == head:
          p.step_text = f'{release} is the current head.'
          continue

        p.step_text = f'Update head to {release}.'
        updates.append((channel, release))

    return updates

  def _get_release(self, channel):
    milestones = [m for m in self.milestones if m['schedule_phase'] == channel]
    if not milestones:
      return None

    milestone = sorted(milestones, key=lambda ms: -ms['milestone'])[0]

    chromium_branch = milestone['chromium_branch']
    stdout = self._git(
        'ls-remote',
        V8_REPO,
        f'refs/heads/chromium/{chromium_branch}',
        name='git: Fetch recent revision')
    return stdout.split('\t')[0]

  def _git(self, *cmd, **kwargs):
    cwd = self.api.v8.checkout_root / 'v8'
    with self.api.context(cwd=cwd):
      return self.api.v8.git_output(*cmd, **kwargs)

  def update_channels(self, updates):
    with self.api.step.nest(f'Update {len(updates)} channel(s)'):
      if not updates:
        return

      self.api.gclient.set_config('v8')
      for channel, revision in updates:
        self.api.v8.checkout(revision)
        with self.api.step.nest(f'Update channel {channel}'):
          self._git('push', 'origin', f'{revision}:refs/heads/{channel}', '-f')
          self.head_revision_by_channel[channel] = revision

  def validate_channels(self):
    with self.api.step.nest('Check freshness'):
      outdated = []
      for channel in self.channels:
        with self.api.step.nest(f'Check {channel} age') as p:
          revision = self.head_revision_by_channel[channel]
          age = self._get_age_sec(revision)
          max_age = MAX_CHANNEL_AGE.get(channel, DEFAULT_AGE)
          p.step_text = f'Current: {age}s · Max: {max_age}s'

          if age > max_age:
            outdated.append(f'{channel} is outdated: {age}s > {max_age}s.')

    return outdated

  def _get_age_sec(self, revision):
    log = self.api.gitiles.commit_log(V8_REPO.rstrip('/'), revision, attempts=5)
    commit_time = dt.datetime.strptime(log['committer']['time'], TIME_FORMAT)
    return int((self.api.time.utcnow() - commit_time).total_seconds())


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
    channels = config.get('channels', ['stable'])
    now = config.get('now', 1729071780)  # 2024-10-16T09:43:00+00

    channel_mocks = []
    for channel in channels:
      channel_mocks.append(
          ls_remote(f'Initialize.git: Fetch head for refs/heads/{channel}',
                    f'refs/heads/{channel}', 'c0ffee'))

      revision = 'c0ffee'
      if channel == 'stable':
        revision = stable_revision

      if channel in {'stable', 'beta'}:
        channel_mocks.append(
            ls_remote(f'Verify {channel}.git: Fetch recent revision',
                      'refs/heads/chromium/129', revision))

      channel_mocks.append(
          api.step_data(
              f'Check freshness.Check {channel} age.commit log: {revision}',
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
      {'channels': ['stable', 'dev']},
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
