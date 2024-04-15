# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re

from . import commons

GERRIT_BASE_URL = 'https://chromium-review.googlesource.com'


class CLManager:

  def __init__(self, api, bugs, gerrit_base_url=GERRIT_BASE_URL):
    self.api = api
    self.bugs = bugs
    self.gerrit_base_url = gerrit_base_url

  def upload_cl(self, subject, upload_flags, commit_msg_lines, add=False):
    """
    Verify that the local checkout is dirty, commit changes and upload a CL with
    the given subject and reviewers. If the local checkout is not dirty, we do
    nothing.

    Returns the URL to the uploaded CL, or None if no CL was uploaded.
    """
    # Check for a difference. If no deps changed, the diff is empty.
    with self.api.context(cwd=self.api.path.checkout_dir):
      step_result = self.api.git(
          'status',
          '-s',
          '-uno',
          stdout=self.api.raw_io.output_text(),
      )
    diff = step_result.stdout.strip()
    step_result.presentation.logs['diff'] = diff.splitlines()

    if not diff:
      return None

    if add:
      # Add all files to the commit
      self.api.git('add', '-A')

    # Create a rolling CL. Ignore submodule updates
    args = ['-c', 'diff.ignoreSubmodules=all', 'commit', '-a', '-m', subject]

    for commit_line in commit_msg_lines:
      args.extend(['-m', commit_line])

    kwargs = {'stdout': self.api.raw_io.output_text()}
    with self.api.context(
        cwd=self.api.path.checkout_dir,
        env_prefixes={'PATH': [self.api.v8.depot_tools_path]}):
      self.api.git(*args, **kwargs)
      self.api.git('show')
      upload_args = [
          'cl',
          'upload',
          '-f',
          '--use-commit-queue',
          '--bypass-hooks',
          '--send-mail',
      ]

      if self.bugs is not None:
        upload_args += ['-b', self.bugs]

      upload_args.extend(upload_flags)
      step_result = self.api.git(
          *upload_args, stdout=self.api.raw_io.output_text())

      # Extract the cl link from stdout
      cl_link = re.search(r'https:\/\/.*\/\+\/\d+', step_result.stdout).group(0)

      return cl_link

  def abandon_active_cls(self, subject):
    """Ensure no other active roll exists. If it does, abandon the old one."""
    commits = self.api.gerrit.get_changes(
        self.gerrit_base_url,
        query_params=[
            ('project', commons.get_project_name(self.api)),
            ('owner', self.api.v8_auto_roller.service_account),
            ('status', 'open'),
            ('subject', f'"{subject}"'),
        ],
        limit=20,
        step_test_data=self.api.gerrit.test_api.get_empty_changes_response_data,
    )

    # Querying gerrit with a subject is not exact, so filter the results for
    # precise match.
    commits = [c for c in commits if c['subject'] == subject]

    for commit in commits:
      self.api.gerrit.abandon_change(
          self.gerrit_base_url,
          commit['_number'],
          'stale roll',
      )
      step_result = self.api.step('Previous roll failed', cmd=None)
      step_result.presentation.step_text = 'Notify sheriffs!'
      step_result.presentation.status = 'FAILURE'
