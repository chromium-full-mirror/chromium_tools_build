# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Set the release branch in refs/heads/<channel> to the most recent version
from chromiumdash."""

from recipe_engine.recipe_api import Property

from recipe_engine import post_process as post

DEPS = [
    'chromiumdash',
    'depot_tools/gclient',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/url',
    'v8',
]

PROPERTIES = {
    # Set the release channels to be updated, e.g. ["stable", "beta"].
    'channels': Property(kind=list, default=None),
}

V8_REPO = 'https://chromium.googlesource.com/v8/v8/'


def RunSteps(api, channels):
  updater = ReleaseBranchUpdater(api)

  updater.init()
  updates = updater.retrieve_updates(channels)
  updater.update_channels(updates)


class ReleaseBranchUpdater:

  def __init__(self, api):
    self.api = api

  def init(self):
    with self.api.step.nest('Initialize'):
      path = self.api.v8.checkout_root / 'v8'
      self.api.file.ensure_directory('ensure v8 checkout dir', path)

  def retrieve_updates(self, channels):
    channels = channels or []
    updates = []
    for channel in channels:
      with self.api.step.nest(f'Verify {channel}') as presentation:
        head = self._get_ref_head(channel)
        release = self._get_release(channel)

        if release == head:
          presentation.step_text = f'{release} is the current head.'
          continue

        presentation.step_text = f'Update head to {release}.'
        updates.append((channel, release))

    return updates

  def _get_release(self, channel):
    milestones = self.api.chromiumdash.milestones(
        100, 'chromiumdash: Fetch recent milestones', only_active=True)
    milestones = [ms for ms in milestones if ms['schedule_phase'] == channel]
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

  def _get_ref_head(self, channel):
    stdout = self._git(
        'ls-remote',
        V8_REPO,
        f'refs/heads/{channel}',
        name='git: Fetch current revision')
    return stdout.split('\t')[0]

  def update_channels(self, updates):
    with self.api.step.nest(f'Update {len(updates)} channel(s)'):
      for channel, release in updates:
        self._update_ref_head(channel, release)

  def _update_ref_head(self, channel, revision):
    with self.api.step.nest(f'Update channel {channel}'):
      self.api.gclient.set_config('v8')
      self.api.v8.checkout(revision)
      self._git('push', 'origin', f'{revision}:refs/heads/{channel}', '-f')


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

  def test(name, current_revision, stable_revision, *args):
    return api.test(
        name,
        api.properties(channels=['stable']),
        ls_remote('Verify stable.git: Fetch current revision',
                  'refs/heads/stable', current_revision),
        api.url.json('Verify stable.chromiumdash: Fetch recent milestones', [
            milestone(129, '6668'),
            milestone(128, '6613'),
            milestone(130, '6723', channel='beta'),
        ]),
        ls_remote('Verify stable.git: Fetch recent revision',
                  'refs/heads/chromium/129', stable_revision),
        *args,
        api.post_process(post.DropExpectation),
    )

  yield test(
      'new-revision',
      'c0ffee',
      '7ea',
      api.post_process(post.StepTextEquals, 'Verify stable',
                       'Update head to 7ea.'),
      api.post_process(post.MustRun,
                       'Update 1 channel(s).Update channel stable'),
  )

  yield test(
      'no-updates',
      'c0ffee',
      'c0ffee',
      api.post_process(post.StepTextEquals, 'Verify stable',
                       'c0ffee is the current head.'),
      api.post_process(post.MustRun, 'Update 0 channel(s)'),
  )
