#!/usr/bin/env vpython
# Copyright (c) 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test cases for upload_perf_dashboard_results_test.py"""

import unittest

import upload_perf_dashboard_results


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


class UploadPerfDashboardResultsTest(unittest.TestCase):
  """Tests related to functions which retrieve perfmastername."""

  def setUp(self):
    super(UploadPerfDashboardResultsTest, self).setUp()
    self.maxDiff = None
    self.parser = upload_perf_dashboard_results._CreateParser()

  def GetPerfDashboardMachineGroup(self, options):
    return upload_perf_dashboard_results._GetMachineGroup(options)

  def testGetMasterName_LUCI_PerfDashboardMasterNameNotSet(self):
    options, _ = self.parser.parse_args(['--is-luci-builder'])

    with self.assertRaises(ValueError):
      self.GetPerfDashboardMachineGroup(options)

  def testGetMasterName_LUCI_PerfDashboardMasterNameSet(self):
    options, _ = self.parser.parse_args(
        ['--is-luci-builder', '--perf-dashboard-machine-group', 'Yoda'])

    self.assertEquals('Yoda', self.GetPerfDashboardMachineGroup(options))


class GetGitRevisionTest(unittest.TestCase):
  """Tests related to getting revisions from a directory."""

  def test_GitSvnCase(self):
    # pylint: disable=W0212
    self.assertEqual(
        upload_perf_dashboard_results._GetGitCommitPositionFromLog(
            CHROMIUM_LOG), '291141')
    # pylint: disable=W0212
    self.assertEqual(
        upload_perf_dashboard_results._GetGitCommitPositionFromLog(BLINK_LOG),
        '180728')


if __name__ == '__main__':
  unittest.main()
