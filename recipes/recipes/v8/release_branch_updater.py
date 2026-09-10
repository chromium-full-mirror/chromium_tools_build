# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Set the release branch in refs/heads/<channel> to the most recent version
from chromiumdash."""

import datetime as dt

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.v8.release_branch_updater import (
  InputProperties,
  ChannelSource,
)

from recipe_engine.recipe_api import Property
from recipe_engine import post_process as post

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromiumdash, v8
from RECIPE_MODULES.depot_tools import gclient, git, gitiles
from RECIPE_MODULES.recipe_engine import (
  context,
  file,
  json,
  path,
  properties,
  raw_io,
  step,
  time,
  url,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromiumdash: chromiumdash.API
  context: context.API
  file: file.API
  gclient: gclient.API
  git: git.API
  gitiles: gitiles.API
  json: json.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  time: time.API
  url: url.API
  v8: v8.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  json: json.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  time: time.TEST_API
  url: url.TEST_API


PROPERTIES = InputProperties

TIME_FORMAT = '%a %b %d %H:%M:%S %Y'
V8_REPO = 'https://chromium.googlesource.com/v8/v8/'


def RunSteps(api: DEPS, props):
  updater = ReleaseBranchUpdater(api, props.channels)
  updater.init()
  channels = updater.retrieve_updates()
  updater.update_channels(channels)

  errors = updater.validate_channels()
  if errors:
    note = '\n'.join(errors)
    return result_pb.RawResult(status=common_pb.FAILURE, summary_markdown=note)


class Channel:
  def __init__(self, api: DEPS, milestones, spec):
    self.api = api
    self.milestones = milestones
    self.spec = spec

    self._current_head = self._fetch_current_head()
    self._next_head = self._fetch_next_head()

  @property
  def refname(self):
    return self.spec.refname

  @property
  def channel(self):
    return self.spec.channel

  @property
  def max_age_sec(self):
    return int(self.spec.max_age_weeks * 7 * 24 * 60 * 60)

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
      self._git(
        'push', 'origin', f'{self.next_head}:refs/heads/{self.refname}', '-f'
      )

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
      name=f'git: Fetch current head for refs/heads/{self.refname}',
    )

  def _fetch_next_head(self):
    raise NotImplementedError()  # pragma: no cover


class MilestoneChannel(Channel):
  def schedule_phase_matches(self, milestone):
    """Match milestone to channel translating the rename of the beta channel
    shortly before the release.
    """
    phase = milestone['schedule_phase']
    return (
      phase == self.channel or phase == 'stable_cut' and self.channel == 'beta'
    )

  def _fetch_next_head(self):
    milestones = [m for m in self.milestones if self.schedule_phase_matches(m)]
    if not milestones:
      return None

    milestone = sorted(milestones, key=lambda ms: -ms['milestone'])[0]
    chromium_branch = milestone['chromium_branch']
    return self.api.git.ls_remote(
      V8_REPO,
      f'refs/heads/chromium/{chromium_branch}',
      name=f'git: Fetch next head for refs/heads/{self.refname}',
    )


class ReleaseChannel(Channel):
  @property
  def platform(self):
    return self.spec.platform

  def _fetch_next_head(self):
    return self.api.chromiumdash.releases(
      self.platform,
      self.channel,
      1,
      f'chromiumdash: Fetch next head for refs/heads/{self.refname}',
    )[0]['hashes']['v8']


class ReleaseBranchUpdater:
  def __init__(self, api: DEPS, channel_specs):
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
    self.api.file.ensure_directory(
      'ensure v8 checkout dir', self.api.v8.checkout_root / 'v8'
    )

  def _fetch_milestones(self):
    if any(c.source == ChannelSource.MILESTONES for c in self.channel_specs):
      self.milestones = self.api.chromiumdash.milestones(
        100, 'chromiumdash: Fetch recent milestones', only_active=True
      )

  def _create_channels(self):
    by_source = {
      ChannelSource.MILESTONES: MilestoneChannel,
      ChannelSource.RELEASES: ReleaseChannel,
    }

    for spec in self.channel_specs:
      channel_cls = by_source[spec.source]
      self.channels.append(channel_cls(self.api, self.milestones, spec))

  def retrieve_updates(self):
    updates = []
    for channel in self.channels:
      with self.api.step.nest(f'Verify {channel.refname}') as p:
        if channel.next_head is None:
          p.step_text = 'Channel is unavailable at chromiumdash.'
          continue

        if channel.next_head == channel.current_head:
          p.step_text = f'Head {channel.next_head} is up-to-date.'
          continue

        p.step_text = f'⇧ Update head to {channel.next_head}.'
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


def GenTests(api: TEST_DEPS):

  def milestone(milestone_number, chromium_branch, channel='stable'):
    return {
      "chromium_branch": chromium_branch,
      "schedule_phase": channel,
      "milestone": milestone_number,
    }

  def ls_remote(step_name, branch, revision):
    return api.override_step_data(
      step_name,
      api.raw_io.stream_output_text(f'{revision}\t{branch}\n', stream='stdout'),
    )

  def test(name, config, *args):
    beta_name = config.get('beta_name', 'beta')
    stable_revision = config.get('stable_revision', '7ea')
    channels = config.get(
      'channels',
      [
        {
          "refname": "stable",
          "source": "MILESTONES",
          "channel": "stable",
          "max_age_weeks": 5,
        }
      ],
    )
    now = config.get('now', 1729071780)  # 2024-10-16T09:43:00+00

    next_revisions_by_ref = {
      'stable': stable_revision,
      'beta': config.get('beta_revision', '7eb'),
      'extended': '50da',
    }

    mocks = []
    if any(c['source'] == "MILESTONES" for c in channels):
      mocks.append(
        api.url.json(
          'Initialize.chromiumdash: Fetch recent milestones',
          [
            milestone(129, '6668'),
            milestone(128, '6613'),
            milestone(130, '6723', channel=beta_name),
          ],
        )
      )

    for channel in channels:
      refname = channel['refname']
      mocks.append(
        ls_remote(
          f'Initialize.git: Fetch current head for refs/heads/{refname}',
          f'refs/heads/{refname}',
          'c0ffee',
        )
      )

      revision = next_revisions_by_ref.get(refname, 'c0ffee')
      if refname in {'stable', 'beta'}:
        chromium_branch = '129'
        if refname == 'beta' and beta_name == 'stable_cut':
          chromium_branch = '6723'
        mocks.append(
          ls_remote(
            f'Initialize.git: Fetch next head for refs/heads/{refname}',
            f'refs/heads/chromium/{chromium_branch}',
            revision,
          )
        )

      if refname == 'extended':
        mocks.append(
          api.url.json(
            'Initialize.chromiumdash: Fetch next head for refs/heads/extended',
            [
              {'hashes': {'v8': '50da'}},
            ],
          )
        )

      mocks.append(
        api.step_data(
          f'Check freshness.Check {refname} age.commit log: {revision}',
          api.json.output({'committer': {'time': 'Wed Oct 16 09:42:00 2024'}}),
        )
      )

    return api.test(
      name,
      api.properties(channels=channels),
      api.time.seed(now),
      api.time.step(0),
      *mocks,
      *args,
      api.post_process(post.DropExpectation),
    )

  yield test(
    'beta-stable-cut',
    {
      "channels": [
        {
          "refname": "beta",
          "source": "MILESTONES",
          "channel": "beta",
          "max_age_weeks": 9,
        }
      ],
      "beta_name": "stable_cut",
    },
    api.post_process(
      post.StepTextEquals, 'Verify beta', '⇧ Update head to 7eb.'
    ),
    api.post_process(post.MustRun, 'Update 1 channel(s).Update channel beta'),
  )

  yield test(
    'new-revision-for-milestones-endpoint',
    {},
    api.post_process(
      post.StepTextEquals, 'Verify stable', '⇧ Update head to 7ea.'
    ),
    api.post_process(post.MustRun, 'Update 1 channel(s).Update channel stable'),
  )

  yield test(
    'new-revision-for-releases-endpoint',
    {
      "channels": [
        {
          "refname": "extended",
          "source": "RELEASES",
          "channel": "Extended",
          "platform": "Mac",
          "max_age_weeks": 9,
        }
      ],
    },
    api.post_process(
      post.StepTextEquals, 'Verify extended', '⇧ Update head to 50da.'
    ),
    api.post_process(
      post.MustRun, 'Update 1 channel(s).Update channel extended'
    ),
  )

  yield test(
    'no-updates',
    {'stable_revision': 'c0ffee'},
    api.post_process(
      post.StepTextEquals, 'Verify stable', 'Head c0ffee is up-to-date.'
    ),
    api.post_process(post.MustRun, 'Update 0 channel(s)'),
  )

  yield test(
    'missing-chromiumdash-channel',
    {
      'channels': [
        {
          "refname": "stable",
          "source": "MILESTONES",
          "channel": "stable",
          "max_age_weeks": 5,
        },
        {
          "refname": "dev",
          "source": "MILESTONES",
          "channel": "dev",
          "max_age_weeks": 5,
        },
      ],
    },
    api.post_process(post.MustRun, 'Update 1 channel(s).Update channel stable'),
    api.post_process(
      post.StepTextEquals,
      'Verify dev',
      'Channel is unavailable at chromiumdash.',
    ),
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
    api.post_process(
      post.SummaryMarkdown, 'stable is outdated: 5274060s > 3024000s.'
    ),
  )
