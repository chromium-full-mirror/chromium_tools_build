# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from collections.abc import Iterable

from recipe_engine import recipe_api

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb


class ChromiumGerritUitlsApi(recipe_api.RecipeApi):
  GITILES_CHROMIUM_SRC_URL = (
    'https://chromium.googlesource.com/chromium/src.git'
  )
  GERRIT_CHROMIUM_SRC_PROJECT = 'chromium/src'
  GERRIT_HOST = 'chromium-review.googlesource.com'
  GERRIT_URL = 'https://' + GERRIT_HOST

  def create_temp_cl(
    self,
    file_path: str,
    topic: str,
    extra_commit_msg_lines: Iterable[str] | None = None,
    git_footers: Iterable[str] | None = None,
  ) -> tuple[common_pb.GerritChange, str]:
    """Creates a throw-away CL in chromium/src.git via Gerrit's REST API.

    The CL is intended to be used for interacting with tryjobs, and not for
    submission. The CL contents will simply consist of whitespace appended to
    the end of the given file.

    Args:
      file_path - File path in chromium/src.git to change.
      topic - Name of the gerrit topic to attach to the CL.
      extra_commit_msg_lines - List of lines to add to the commit message.
      git_footers - Like extra_commit_msg_lines, but list of lines to put at
        the very end of the commit msg.

    Returns tuple of (buildbucket.common.GerritChange of the CL, full URL of
      the CL)
    """
    extra_commit_msg_lines = extra_commit_msg_lines or []
    git_footers = git_footers or []
    old_contents = self.m.gitiles.download_file(
      self.GITILES_CHROMIUM_SRC_URL,
      file_path,
      step_test_data=lambda: self.m.json.test_api.output({'value': ''}),
    )

    # Add the whitespace to the end of the file, since adding it add the top might
    # make some copyright header detection checks fail.
    new_contents = old_contents + '\n'
    new_contents_by_file_path = {file_path: new_contents}
    commit_msg_lines = [
      'Test commit; testing a recipe CL',
      '',
      'This CL was uploaded for the purposes of testing a recipe CL on',
      'Chromium trybots.',
      '',
      *extra_commit_msg_lines,
      f'Created by https://ci.chromium.org/ui/b/{self.m.buildbucket.build.id}',
      '',
      'Bug: None',
      # 'Commit: false' to prevent someone from accidentally submitting the CL.
      'Commit: false',
      *git_footers,
      '',
    ]
    change_info = self.m.gerrit.update_files(
      self.GERRIT_URL,
      self.GERRIT_CHROMIUM_SRC_PROJECT,
      'main',
      new_contents_by_file_path,
      '\n'.join(commit_msg_lines),
      params=['work_in_progress=true', 'notify=NONE', f'topic={topic}'],
    )
    change_num = int(change_info['_number'])
    gerrit_change = common_pb.GerritChange(
      host=self.GERRIT_HOST,
      project=change_info['project'],
      change=change_num,
      # We hardcode '2' here since the current_revision_number field returned
      # by Gerrit can sometimes not be the latest patchset.
      patchset=2,
    )
    return (
      gerrit_change,
      f'{self.GERRIT_URL}/c/{self.GERRIT_CHROMIUM_SRC_PROJECT}/+/{change_num}',
    )

  def abandon_cl(self, change_num: str):
    """Abandons a chromium/src.git CL via Gerrit's REST API.

    Args:
      change_num - Gerrit issue num of the chromium/src.git CL to abandon.
    """
    self.m.gerrit.abandon_change(
      self.GERRIT_URL, change_num, name=f'abandon {change_num}'
    )

  def abandon_old_cls(self, topic: str, age: str):
    """Abandons all CLs tagged with the given topic older than the given age.

    Useful for cleaning up any CLs lingering from previous builds.

    Args:
      topic - Name of the gerrit topic that will be queried for in abandoning.
      age - Minimum age of CLs to abandon. For syntax of this arg, see:
          https://gerrit-review.googlesource.com/Documentation/user-search.html#_search_operators
    """
    with self.m.step.nest('abandon old CLs'):
      query_params = [
        ('status', 'open'),
        ('topic', topic),
        ('age', age),
        ('author', self.m.buildbucket.swarming_task_service_account),
      ]
      changes = self.m.gerrit.get_changes(
        self.GERRIT_URL, query_params=query_params
      )
      for change in changes:
        # There might be a race condition with other concurrent builds trying to
        # clean-up the same old CLs. So just swallow all errors to prevent that
        # from crashing the build.
        try:
          self.abandon_cl(change['_number'])
        except self.m.step.StepFailure:
          pass
