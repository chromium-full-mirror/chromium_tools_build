#!/usr/bin/env vpython3
# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from typing import Set

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

      if commands == ['git', 'rev-parse', 'HEAD']:
        return '123abc'
      assert False, 'Unexpected subprocess call'

    mock_subprocess.side_effect = mock_subprocess_side_effect

    file_revisions = repository_util._GetFileRevisions(
        '/src', 'DEPS', ['//file1.cc', '//third_party/repo/file2.cc'])

    expected_file_revisions = {
        '//file1.cc': ('file1hash', 12345),
        '//third_party/repo/file2.cc': ('file2hash', 12345)
    }
    self.assertDictEqual(expected_file_revisions, file_revisions)

  @mock.patch('repository_util.subprocess.Popen', autospec=True)
  @mock.patch('repository_util.subprocess.check_output', autospec=True)
  def test_get_file_revisions_batching(self, mock_check_output, mock_popen):
    files = ['//file%d.cc' % i for i in range(1501)]

    def mock_check_output_side_effect(commands, cwd, text=None):
      cwd = os.path.normpath(cwd)
      if commands[:2] == ['git', 'ls-files']:
        if cwd == '/src':
          return '\n'.join(['file%d.cc' % i for i in range(1501)])
        if cwd == '/src/third_party/repo':
          return ''
      if commands == ['git', 'rev-parse', 'HEAD']:
        return '123abc'
      assert False, 'Unexpected subprocess call: %s' % commands

    mock_check_output.side_effect = mock_check_output_side_effect

    mock_process = mock.Mock()
    mock_popen.return_value = mock_process

    log_output = []
    for i in range(1501):
      log_output.append('COMMIT hash%d 12345' % i)
      log_output.append('file%d.cc' % i)

    mock_process.stdout = iter(log_output)
    mock_process.wait.return_value = 0

    file_revisions = repository_util._GetFileRevisions('/src', 'DEPS', files)

    self.assertEqual(len(file_revisions), 1501)
    for i in range(1501):
      self.assertEqual(file_revisions['//file%d.cc' % i], ('hash%d' % i, 12345))

  @mock.patch('repository_util.subprocess.Popen', autospec=True)
  @mock.patch('repository_util.subprocess.check_output', autospec=True)
  def test_get_file_revisions_no_batching(self, mock_check_output, mock_popen):
    files = ['//file%d.cc' % i for i in range(5)]

    def mock_check_output_side_effect(commands, cwd, text=None):
      cwd = os.path.normpath(cwd)
      if commands[:2] == ['git', 'ls-files']:
        if cwd == '/src':
          return '\n'.join(['file%d.cc' % i for i in range(5)])
        if cwd == '/src/third_party/repo':
          return ''
      if commands[:-1] == ['git', 'log', '-n', '1', '--pretty=format:%H:%ct']:
        path = commands[-1]
        i = int(path[4:-3])
        return 'hash%d:12345' % i
      if commands == ['git', 'rev-parse', 'HEAD']:
        return '123abc'
      assert False, 'Unexpected subprocess call'

    mock_check_output.side_effect = mock_check_output_side_effect

    file_revisions = repository_util._GetFileRevisions('/src', 'DEPS', files)

    self.assertEqual(len(file_revisions), 5)
    self.assertFalse(mock_popen.called)

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


@mock.patch('subprocess.check_output')
class RevisionCacheTest(fake_filesystem_unittest.TestCase):

  def setUp(self):
    super().setUp()
    self.setUpPyfakefs()
    self.cache_path = os.path.join(tempfile.gettempdir(), '.cov-rev-cache',
                                   '123abc.csv')

  def test_absent(self, mock_check_output):
    mock_check_output.side_effect = ['123abc:1111\n', '789def:2222\n']
    cache = repository_util.RevisionCache(self.cache_path)

    cache.TryLoad()
    self.assertEqual(('//file1.cc', '123abc', 1111),
                     cache.RetrieveRevision(
                         ('/path/to/src', '//', '//file1.cc')))
    self.assertEqual(('//third_party/fake-submodule/file2.cc', '789def', 2222),
                     cache.RetrieveRevision(
                         ('/path/to/src', '//third_party/fake-submodule/',
                          '//third_party/fake-submodule/file2.cc')))

    cache.Flush()
    self.verify_cache({
        '//,file1.cc,123abc,1111\n',
        '//third_party/fake-submodule/,file2.cc,789def,2222\n',
    })

  def test_present(self, mock_check_output):
    self.fs.create_file(
        self.cache_path,
        contents=('//,file1.cc,123abc,1111\n'
                  '//third_party/fake-submodule/,file2.cc,789def,2222\n'))
    mock_check_output.side_effect = subprocess.SubprocessError
    cache = repository_util.RevisionCache(self.cache_path)

    cache.TryLoad()
    self.assertTrue(os.path.exists(self.cache_path))
    self.assertEqual(('//file1.cc', '123abc', 1111),
                     cache.RetrieveRevision(
                         ('/path/to/src', '//', '//file1.cc')))
    self.assertEqual(('//third_party/fake-submodule/file2.cc', '789def', 2222),
                     cache.RetrieveRevision(
                         ('/path/to/src', '//third_party/fake-submodule/',
                          '//third_party/fake-submodule/file2.cc')))

    cache.Flush()
    self.verify_cache({
        '//,file1.cc,123abc,1111\n',
        '//third_party/fake-submodule/,file2.cc,789def,2222\n',
    })

  def test_corrupt(self, mock_check_output):
    # Simulate an incomplete write
    self.fs.create_file(self.cache_path, contents='//,file.cc,')
    mock_check_output.side_effect = ['123abc:1111\n', '789def:2222\n']
    cache = repository_util.RevisionCache(self.cache_path)

    cache.TryLoad()
    self.assertFalse(os.path.exists(self.cache_path))
    self.assertEqual(('//file1.cc', '123abc', 1111),
                     cache.RetrieveRevision(
                         ('/path/to/src', '//', '//file1.cc')))
    self.assertEqual(('//third_party/fake-submodule/file2.cc', '789def', 2222),
                     cache.RetrieveRevision(
                         ('/path/to/src', '//third_party/fake-submodule/',
                          '//third_party/fake-submodule/file2.cc')))

    cache.Flush()
    self.verify_cache({
        '//,file1.cc,123abc,1111\n',
        '//third_party/fake-submodule/,file2.cc,789def,2222\n',
    })

  @mock.patch('subprocess.Popen')
  def test_consistency_batch_vs_individual(self, mock_popen, mock_check_output):
    files_data = [
        ('//file1.cc', 'hash1', 12345),
        ('//dir/file2.cc', 'hash2', 67890),
    ]

    # 1. Run BatchRetrieve
    cache_batch = repository_util.RevisionCache(self.cache_path)
    cache_batch.TryLoad()

    mock_process = mock.Mock()
    mock_popen.return_value = mock_process

    # Simulating git log output for batch: newest first
    log_output = [
        'COMMIT hash2 67890\n',
        'dir/file2.cc\n',
        'COMMIT hash1 12345\n',
        'file1.cc\n',
    ]
    mock_process.stdout = iter(log_output)
    mock_process.wait.return_value = 0

    paths_in_checkout = [f[0][2:] for f in files_data]
    cache_batch.BatchRetrieve('/path/to/src', '//', paths_in_checkout)

    # 2. Run RetrieveRevision (individual)
    cache_indiv = repository_util.RevisionCache(self.cache_path)
    cache_indiv.TryLoad()

    def mock_check_output_side_effect(cmd, cwd, text=None):
      path = cmd[-1]
      if path == 'file1.cc':
        return 'hash1:12345\n'
      if path == 'dir/file2.cc':
        return 'hash2:67890\n'
      raise ValueError('Unexpected path: %s' % path)

    mock_check_output.side_effect = mock_check_output_side_effect

    for path, _, _ in files_data:
      cache_indiv.RetrieveRevision(('/path/to/src', '//', path))

    # 3. Compare cache entries
    self.assertDictEqual(cache_batch._entries, cache_indiv._entries)

    # Also assert they match the expected data
    expected_entries = {
        ('//', 'file1.cc'): ('hash1', 12345),
        ('//', 'dir/file2.cc'): ('hash2', 67890),
    }
    self.assertDictEqual(expected_entries, cache_batch._entries)

  def verify_cache(self, expected_lines: Set[str]):
    # Tests should be insensitive to CSV line order.
    with open(self.cache_path) as cache_file:
      self.assertEqual(expected_lines, set(cache_file))


class ParseGitLogStreamTest(unittest.TestCase):

  def test_happy_path(self):
    log_output = [
        'COMMIT hash1 12345\n',
        'file1.cc\n',
        'file2.cc\n',
        'COMMIT hash2 67890\n',
        'file3.cc\n',
    ]
    expected = [
        ('hash1', 12345, 'file1.cc'),
        ('hash1', 12345, 'file2.cc'),
        ('hash2', 67890, 'file3.cc'),
    ]
    self.assertEqual(
        list(repository_util._ParseGitLogStream(log_output)), expected)

  def test_empty_lines_ignored(self):
    log_output = [
        '\n',
        'COMMIT hash1 12345\n',
        '\n',
        'file1.cc\n',
        '\n',
    ]
    expected = [
        ('hash1', 12345, 'file1.cc'),
    ]
    self.assertEqual(
        list(repository_util._ParseGitLogStream(log_output)), expected)

  def test_malformed_commit_skipped(self):
    log_output = [
        'COMMIT hash1\n',  # Malformed, missing timestamp
        'file1.cc\n',
        'COMMIT hash2 67890\n',
        'file2.cc\n',
    ]
    expected = [
        ('hash2', 67890, 'file2.cc'),
    ]
    self.assertEqual(
        list(repository_util._ParseGitLogStream(log_output)), expected)

  def test_file_before_commit_skipped(self):
    log_output = [
        'file1.cc\n',  # Before any COMMIT
        'COMMIT hash1 12345\n',
        'file2.cc\n',
    ]
    expected = [
        ('hash1', 12345, 'file2.cc'),
    ]
    self.assertEqual(
        list(repository_util._ParseGitLogStream(log_output)), expected)


if __name__ == '__main__':
  unittest.main()
