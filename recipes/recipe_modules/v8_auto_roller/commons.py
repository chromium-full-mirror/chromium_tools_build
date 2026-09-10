# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re


def commit_msg_lines_w_reviewes(commit_msg_lines, reviewers):
  lines = [
    (
      'This roll requires a manual review. See http://go/reviewed-rolls for '
      'guidance.'
    )
  ] + commit_msg_lines

  if reviewers:
    lines.append(f'R={",".join(reviewers)}')

  return lines


def roll_origin_line(api):
  return f'\nRoll created at {api.buildbucket.build_url()}'


def discard_local_changes(api, source_dir):
  with api.context(
    cwd=source_dir, env_prefixes={'PATH': [api.v8.depot_tools_path(source_dir)]}
  ):
    api.git('checkout', '-f', 'origin/main')
    api.git('branch', '-D', 'roll', ok_ret='any')
    api.git('clean', '-ffd')
    api.git('new-branch', 'roll')


def get_targeted_solution(api):
  # Get the first solution defined in gclient, which is the target solution.
  return api.gclient.c.solutions[0]


def get_project_name(api):
  # This works by convention. Verify and update when adding new projects.
  # {base}/{name_part_1}/{name_part_2}
  target = get_targeted_solution(api)
  return '/'.join(target.url.split('/')[-2:])
