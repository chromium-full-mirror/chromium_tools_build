#!/usr/bin/env vpython3
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import unittest
import sys


ROOT_DIR = os.path.normpath(os.path.join(__file__, '..', '..', '..'))
sys.path.extend(
  [
    os.path.join(ROOT_DIR, 'recipes'),
    os.path.join(ROOT_DIR, 'scripts'),
  ]
)

import extract_build
import bot_utils


class MockOptions:
  build_properties = {}
  build_archive_url = None


class ExtractBuildTest(unittest.TestCase):
  def setUp(self):
    self._build_revision = 123

  def testGetBuildUrl(self):
    options = MockOptions()

    base_filename, version_suffix = bot_utils.GetZipFileNames(
      self._build_revision
    )

    gs_url_without_slash = 'gs://foo/Win'
    gs_url_with_slash = 'gs://foo/Win/'
    gs_url_with_filename = 'gs://foo/Win/%s.zip' % base_filename
    expected_gs_url = (
      gs_url_with_slash + base_filename + version_suffix + '.zip'
    )

    # Verify that only one slash is added: URL without ending slash.
    self._VerifyBuildUrl(options, gs_url_without_slash, expected_gs_url)

    # URL with ending slash.
    self._VerifyBuildUrl(options, gs_url_with_slash, expected_gs_url)

    # URL with filename.
    self._VerifyBuildUrl(options, gs_url_with_filename, expected_gs_url)

  def _VerifyBuildUrl(self, options, url_template, expected_url):
    options.build_url = url_template

    # The versioned_url part of the tuple returned is not tested, since it would
    # just be to copy implementation from extract_build.py into this test.
    url, _archive_name = extract_build.GetBuildUrl(
      options, build_revision=self._build_revision
    )
    self.assertEqual(url, expected_url)


if __name__ == '__main__':
  unittest.main()
