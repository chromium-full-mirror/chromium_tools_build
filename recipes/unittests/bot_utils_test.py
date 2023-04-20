#!/usr/bin/env vpython
# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import sys
import unittest

import mock

ROOT_DIR = os.path.normpath(os.path.join(__file__, '..', '..', '..'))
sys.path.extend([
    os.path.join(ROOT_DIR, 'scripts'),
    os.path.join(ROOT_DIR, 'recipes'),
])

import bot_utils
from common import chromium_utils

_BUILD_DIR = ROOT_DIR

# Note: The git-svn id / cr pos is intentionally modified.
# Also commit messages modified to be < 80 char.
# TODO(eyaich): Udpate example logs as they are out of date.  Source of truth
# is now git so the git-svn-id entry is no longer present.  Remove BLINK_LOG
# once we remove the blink revision concept
CHROMIUM_LOG = """
Update GPU rasterization device whitelist

This replaces the whitelisting of all Qualcomm GPUs on
Android 4.4 with whitelisting all Android 4.4 devices
with GL ES version >= 3.0.

BUG=405646

Review URL: https://codereview.chromium.org/468103003

Cr-Commit-Position: refs/heads/main@{#291141}
git-svn-id: svn://svn.chromium.org/chrome/trunk/src@291140 0039d316-1c4b-4281
"""

BLINK_LOG = """
[Sheriff-o-matic] Remove race condition on the commit list.

By always modifying the same list of commits, we ensure that data binding

As well, renamed "revisionLog" to "commitLog" everywhere, to better reflect

BUG=405327
NOTRY=true

Review URL: https://codereview.chromium.org/485253004

git-svn-id: svn://svn.chromium.org/blink/trunk@180728 bbb929c8-8fbe-4397-9dbb-9
"""


class TestGetZipFileNames(unittest.TestCase):

  def setUp(self):
    super(TestGetZipFileNames, self).setUp()
    chromium_utils.OverridePlatformName(sys.platform)

  def testNormalBuildName(self):
    (base_name, version_suffix) = bot_utils.GetZipFileNames('', None, None, 123)
    self._verifyBaseName(base_name)
    self.assertEqual('_123', version_suffix)

  def testNormalBuildNameTryBot(self):
    (base_name, version_suffix) = bot_utils.GetZipFileNames(
        'master.tryserver.chromium.linux', 666, None, 123
    )
    self._verifyBaseName(base_name)
    self.assertEqual('_666', version_suffix)

  def testNormalBuildNameTryBotExtractNoParentBuildNumber(self):

    def dummy():
      bot_utils.GetZipFileNames(
          'master.tryserver.chromium.linux', 666, None, 123, extract=True
      )

    self.assertRaises(Exception, dummy)

  def testNormalBuildNameTryBotExtractWithParentBuildNumber(self):
    (base_name, version_suffix) = bot_utils.GetZipFileNames(
        'master.tryserver.chromium.linux', 666, 999, 123, extract=True
    )
    self._verifyBaseName(base_name)
    self.assertEqual('_999', version_suffix)

  def _verifyBaseName(self, base_name):
    self.assertEqual('full-build-%s' % sys.platform, base_name)


class TestGetBuildRevisions(unittest.TestCase):

  def testNormal(self):
    build_revision = bot_utils.GetBuildRevisions(_BUILD_DIR)
    self.assertTrue(build_revision > 0)

  def testWebKitDir(self):
    build_revision = bot_utils.GetBuildRevisions(_BUILD_DIR)
    self.assertTrue(build_revision > 0)

  def testRevisionDir(self):
    build_revision = bot_utils.GetBuildRevisions(
        _BUILD_DIR, revision_dir=_BUILD_DIR
    )
    self.assertTrue(build_revision > 0)


@mock.patch(
    '__main__.bot_utils._GSUtilSetup',
    mock.MagicMock(side_effect=lambda: ['/mock/gsutil'])
)
@mock.patch('__main__.chromium_utils.RunCommand')
class TestGSUtil(unittest.TestCase):

  def testGSUtilCopyCacheControl(
      self,  # pylint: disable=no-self-use
      run_command_mock,
  ):
    bot_utils.GSUtilCopyFile('foo', 'bar', cache_control='mock_cache')
    run_command_mock.assert_called_with([
        '/mock/gsutil',
        '-h',
        'Cache-Control:mock_cache',
        'cp',
        'file://foo',
        'file://bar/foo',
    ])

  def testGSUtilCopyFileWithDestFilename(
      self,  # pylint: disable=no-self-use
      run_command_mock,
  ):
    bot_utils.GSUtilCopyFile(
        '/my/local/path/foo.txt',
        'gs://bucket/dest/dir',
        dest_filename='bar.txt',
    )
    run_command_mock.assert_called_with([
        '/mock/gsutil',
        'cp',
        'file:///my/local/path/foo.txt',
        'gs://bucket/dest/dir/bar.txt',
    ])

  def testGSUtilCopyFileWithQuietFlag(
      self,  # pylint: disable=no-self-use
      run_command_mock,
  ):
    bot_utils.GSUtilCopyFile('foo', 'bar', add_quiet_flag=True)
    run_command_mock.assert_called_with([
        '/mock/gsutil',
        '-q',
        'cp',
        'file://foo',
        'file://bar/foo',
    ])


class GetGitRevisionTest(unittest.TestCase):
  """Tests related to getting revisions from a directory."""

  def test_GitSvnCase(self):
    # pylint: disable=W0212
    self.assertEqual(
        bot_utils._GetGitCommitPositionFromLog(CHROMIUM_LOG), '291141'
    )
    # pylint: disable=W0212
    self.assertEqual(
        bot_utils._GetGitCommitPositionFromLog(BLINK_LOG), '180728'
    )


if __name__ == '__main__':
  unittest.main()
