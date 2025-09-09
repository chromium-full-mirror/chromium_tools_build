#!/usr/bin/env vpython3
# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import subprocess
import sys
import textwrap
import unittest

import mock
from pyfakefs import fake_filesystem_unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,
                os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'resources')))

import repository_util


class RepositoryUtilTest(fake_filesystem_unittest.TestCase):

  def setUp(self):
    super().setUp()
    self.setUpPyfakefs()
    deps_file_content = textwrap.dedent('''
      vars = {
        'chromium_git': 'https://chromium.googlesource.com',
        'cros_boards': Str(''),
      }

      deps = {
        'src/third_party/repo':
          Var('chromium_git') + '/repo.git' + '@' + 'abcd1234',
      }''')
    self.fs.create_file('/src/DEPS', contents=deps_file_content)
    self.fs.create_dir('/src/third_party/repo')

  @mock.patch('repository_util.subprocess.check_output', autospec=True)
  def test_get_file_revisions(self, mock_subprocess):

    def mock_subprocess_side_effect(commands, cwd, text=None):
      cwd = os.path.normpath(cwd)
      if commands[:2] == ['git', 'ls-files']:
        if cwd == '/src':
          return 'file1.cc'
        if cwd == '/src/third_party/repo':
          return 'file2.cc'

      if commands[:-1] == ['git', 'log', '-n', '1', '--pretty=format:%H:%ct']:
        if commands[-1] == 'file1.cc' and cwd == '/src':
          return 'file1hash:12345'
        if commands[-1] == 'file2.cc' and cwd == '/src/third_party/repo':
          return 'file2hash:12345'

      assert False, 'Unexpected subprocess call'

    mock_subprocess.side_effect = mock_subprocess_side_effect

    file_revisions = repository_util._GetFileRevisions(
        '/src', 'DEPS', ['//file1.cc', '//third_party/repo/file2.cc'])

    expected_file_revisions = {
        '//file1.cc': ('file1hash', 12345),
        '//third_party/repo/file2.cc': ('file2hash', 12345)
    }
    self.assertDictEqual(expected_file_revisions, file_revisions)

  @mock.patch.object(repository_util, '_GetFileRevisions', autospec=True)
  def test_add_git_revisions_to_coverage_files_metadata(
      self, mock_get_file_revisions):
    mock_get_file_revisions.return_value = {
        '//dir1/file1.cc': ('hash1', 1234),
        '//dir2/file2.cc': ('hash2', 5678),
    }

    coverage_files_data = [{'path': '//dir1/file1.cc'}]
    repository_util.AddGitRevisionsToCoverageFilesMetadata(
        coverage_files_data, '/src_path', 'DEPS')
    expected_coverage_files_data = [{
        'path': '//dir1/file1.cc',
        'revision': 'hash1',
        'timestamp': 1234,
    }]
    self.assertListEqual(expected_coverage_files_data, coverage_files_data)

  @mock.patch('repository_util.subprocess.check_output', autospec=True)
  def test_get_unmodified_lines_since_commit(self, mock_subprocess):
    reference_commit = 'hash'
    src_path = '//chromium/src'
    file_path = 'base/myfile.cc'
    diff_output = '\n'.join([
        'diff --git a/newfile b/newfile',
        'index cdf28dbec898..a92d664bc20a 100644', '--- a/newfile',
        '+++ b/newfile', '@@ -1,4 +1,3 @@', '-line 0', ' line 1',
        '-line 3 modified', '-line 4', '+line 2', '+line 3'
    ])
    head_content = '\n'.join(['line 0', 'line 1', 'line 3 modified', 'line 4'])
    reference_commit_content = '\n'.join(['line 1', 'line 2', 'line 3'])

    def mock_subprocess_side_effect(commands, cwd, text=None):
      assert cwd == src_path
      if commands[:2] == ['git', 'diff']:
        assert commands[2:] == ['HEAD', reference_commit, '--', file_path]
        return diff_output

      if commands[:2] == ['git', 'show']:
        show_arg = commands[2].split(':')
        assert len(show_arg) == 2
        assert show_arg[1] == file_path
        if show_arg[0] == 'HEAD':
          return head_content
        if show_arg[0] == reference_commit:
          return reference_commit_content
        assert False, 'Unexpected git show call'

      assert False, 'Unexpected subprocess call'

    mock_subprocess.side_effect = mock_subprocess_side_effect
    actual_unmodified_lines = repository_util.GetUnmodifiedLinesSinceCommit(
        src_path, file_path, reference_commit)
    self.assertListEqual([2], actual_unmodified_lines)


if __name__ == '__main__':
  unittest.main()
