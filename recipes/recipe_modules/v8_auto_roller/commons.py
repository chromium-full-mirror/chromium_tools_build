# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re


def upload_cl(api,
              subject,
              upload_flags,
              commit_msg_lines,
              bugs_label,
              add=False):
  """
  Verify that the local checkout is dirty, commit changes and upload a CL with
  the given subject and reviewers. If the local checkout is not dirty, we do
  nothing.

  Returns the URL to the uploaded CL, or None if no CL was uploaded.
  """
  # Check for a difference. If no deps changed, the diff is empty.
  with api.context(cwd=api.path['checkout']):
    step_result = api.git(
        'status',
        '-s',
        '-uno',
        stdout=api.raw_io.output_text(),
    )
  diff = step_result.stdout.strip()
  step_result.presentation.logs['diff'] = diff.splitlines()

  if not diff:
    return None

  if add:
    # Add all files to the commit
    api.git('add', '-A')

  # Create a rolling CL. Ignore submodule updates
  args = ['-c', 'diff.ignoreSubmodules=all', 'commit', '-a', '-m', subject]

  for commit_line in commit_msg_lines:
    args.extend(['-m', commit_line])

  kwargs = {'stdout': api.raw_io.output_text()}
  with api.context(
      cwd=api.path['checkout'],
      env_prefixes={'PATH': [api.v8.depot_tools_path]}):
    api.git(*args, **kwargs)
    api.git('show')
    upload_args = [
        'cl',
        'upload',
        '-f',
        '--use-commit-queue',
        '--bypass-hooks',
        '--send-mail',
    ]

    if bugs_label is not None:
      upload_args += ['-b', bugs_label]

    upload_args.extend(upload_flags)
    step_result = api.git(*upload_args, stdout=api.raw_io.output_text())

    # Extract the cl link from stdout
    cl_link = re.search(r'https:\/\/.*\/\+\/\d+', step_result.stdout).group(0)

    return cl_link


def commit_msg_lines_w_reviewes(commit_msg_lines, reviewers):
  return [
      ('This roll requires a manual review. See http://go/reviewed-rolls for '
       'guidance.')
  ] + commit_msg_lines + [f'R={",".join(reviewers)}']


def roll_origin_line(api):
  return f'\nRoll created at {api.buildbucket.build_url()}'


def discard_local_changes(api):
  with api.context(
      cwd=api.path['checkout'],
      env_prefixes={'PATH': [api.v8.depot_tools_path]}):
    api.git('checkout', '-f', 'origin/main')
    api.git('branch', '-D', 'roll', ok_ret='any')
    api.git('clean', '-ffd')
    api.git('new-branch', 'roll')
