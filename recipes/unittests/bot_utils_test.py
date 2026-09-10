#!/usr/bin/env python3
# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import sys
import unittest
from unittest import mock

ROOT_DIR = os.path.normpath(os.path.join(__file__, '..', '..', '..'))
sys.path.extend(
  [
    os.path.join(ROOT_DIR, 'scripts'),
    os.path.join(ROOT_DIR, 'recipes'),
  ]
)

import bot_utils
from common import chromium_utils

_BUILD_DIR = ROOT_DIR


class TestGetZipFileNames(unittest.TestCase):
  def setUp(self):
    super().setUp()
    chromium_utils.OverridePlatformName(sys.platform)

  def testNormalBuildName(self):
    (base_name, version_suffix) = bot_utils.GetZipFileNames(123)
    self.assertEqual(base_name, f'full-build-{sys.platform}')
    self.assertEqual('_123', version_suffix)


class TestGetBuildRevisions(unittest.TestCase):
  def testNormal(self):
    build_revision = bot_utils.GetBuildRevisions(_BUILD_DIR)
    self.assertTrue(build_revision)

  def testRevisionDir(self):
    build_revision = bot_utils.GetBuildRevisions(
      _BUILD_DIR, revision_dir=_BUILD_DIR
    )
    self.assertTrue(build_revision)


@mock.patch(
  '__main__.bot_utils._GSUtilSetup',
  mock.MagicMock(side_effect=lambda: ['/mock/gsutil']),
)
@mock.patch('__main__.chromium_utils.RunCommand')
class TestGSUtil(unittest.TestCase):
  def testGSUtilCopyCacheControl(
    self,  # pylint: disable=no-self-use
    run_command_mock,
  ):
    bot_utils.GSUtilCopyFile('foo', 'bar', cache_control='mock_cache')
    run_command_mock.assert_called_with(
      [
        '/mock/gsutil',
        '-h',
        'Cache-Control:mock_cache',
        'cp',
        'file://foo',
        'file://bar/foo',
      ]
    )

  def testGSUtilCopyFileWithDestFilename(
    self,  # pylint: disable=no-self-use
    run_command_mock,
  ):
    bot_utils.GSUtilCopyFile(
      '/my/local/path/foo.txt',
      'gs://bucket/dest/dir',
      dest_filename='bar.txt',
    )
    run_command_mock.assert_called_with(
      [
        '/mock/gsutil',
        'cp',
        'file:///my/local/path/foo.txt',
        'gs://bucket/dest/dir/bar.txt',
      ]
    )

  def testGSUtilCopyFileWithQuietFlag(
    self,  # pylint: disable=no-self-use
    run_command_mock,
  ):
    bot_utils.GSUtilCopyFile('foo', 'bar', add_quiet_flag=True)
    run_command_mock.assert_called_with(
      [
        '/mock/gsutil',
        '-q',
        'cp',
        'file://foo',
        'file://bar/foo',
      ]
    )


if __name__ == '__main__':
  unittest.main()
