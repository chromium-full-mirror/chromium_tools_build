# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Utility to generate blame list data."""

from __future__ import annotations

from collections import defaultdict
import logging
import re
import subprocess

BLAME_LIST_HEADER_REGEX = re.compile(r'<(.+)>\s(.+)\s(.+)\s(.+)\s+(.+)')


def generate_blame_list(src_path, files, num_weeks=4):
  """Generates blame list data for given set of files.

  Args:
    src_path(str): Absolute path to chromium/src checkout dir
    files(list): Files for whose blame list data is to be
          generated. Each file path should be relative to
          chromium/src checkout dir and must start with
          a double-slash(//)
    num_weeks(int): Time period to consider for blame list
          generation.

  Returns a dict which looks like following
  {
    '//abc/myfile1.cc': {...},
    '//abc/myfile2.cc': {...}
  }

  The keys in the dict are file names, values is another dict,
  where keys are author emails and values are lists containing
  line numbers modified by the author in past num_weeks.
  """
  response = {}
  for file_name in files:
    assert file_name.startswith('//')
    # remove leading double-slash(//) from file name while invoking command
    cmd = ['git', 'blame', '-e', '--since=%d.weeks' % num_weeks, file_name[2:]]
    try:
      blame_output = subprocess.check_output(cmd, cwd=src_path, text=True)
      blame_lines = blame_output.splitlines()
      blame_list = _parse_blame_list(blame_lines)
      if blame_list:
        response[file_name] = blame_list
    except subprocess.CalledProcessError as e:
      logging.error('Unable to calculate blame list for file %s' % file_name)
      logging.error(e.output)
  return response


def _parse_blame_list(lines):
  """Parses git blame output."""
  response = defaultdict(list)
  for line in lines:
    if line.startswith('^'):
      continue
    blame_list_header = line[line.find('(') + 1 : line.find(')')]
    match = BLAME_LIST_HEADER_REGEX.match(blame_list_header)
    if not match:
      logging.error(line)
      raise RuntimeError('Unrecognized blame list format')
    author = match.group(1)
    line_number = int(match.group(5))
    response[author].append(line_number)
  return dict(response)
