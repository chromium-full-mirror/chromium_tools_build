#!/usr/bin/env vpython3
# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import optparse
import os
import shutil
import sys
import tempfile
import unittest

ROOT_DIR = os.path.normpath(os.path.join(__file__, '..', '..', '..'))
sys.path.extend(
  [
    os.path.join(ROOT_DIR, 'scripts'),
    os.path.join(ROOT_DIR, 'recipes'),
  ]
)

import zip_build
from common import chromium_utils


def _build_dir(options):
  return os.path.join(options.src_dir, 'out', options.target)


def _setup_testdir(testdir):
  option_parser = optparse.OptionParser()
  zip_build.AddOptions(option_parser)
  options, _ = option_parser.parse_args([])

  options.target = 'Debug'
  options.src_dir = os.path.join(testdir, 'src')
  options.staging_dir = os.path.join(testdir, 'staging')
  options.build_url = 'gs://fake-gs-bucket'
  options.build_revision = 'dolor'

  build_dir = _build_dir(options)
  os.makedirs(build_dir)
  # Create mojo bindings file.
  mojo_bindings_path = os.path.join(build_dir, zip_build.MOJO_BINDINGS_PATH)
  mojo_bindings_dir = os.path.dirname(mojo_bindings_path)
  os.makedirs(mojo_bindings_dir)
  open(mojo_bindings_path, 'w').close()
  # Generate fake mojom files.
  for search_dir in zip_build.MOJOM_SEARCH_DIRS:
    search_path = os.path.join(build_dir, search_dir)
    if not os.path.exists(search_path):
      os.makedirs(search_path)
    tempfile.mkstemp(suffix='.mojom.js', dir=search_path)
    tempfile.mkstemp(suffix='_mojom.py', dir=search_path)
  # Generate a fake layout test data file.
  layout_test_data_dir = os.path.join(build_dir, zip_build.LAYOUT_TEST_DATA_DIR)
  os.makedirs(layout_test_data_dir)
  tempfile.mkstemp(dir=layout_test_data_dir)

  return options


class TestWriteRevisionFile(unittest.TestCase):
  def testWriteFile(self):
    tempdir = tempfile.mkdtemp()
    revision = '123'
    try:
      revision_filename = zip_build.WriteRevisionFile(tempdir, revision)

      self.assertEqual(revision, open(revision_filename).read().strip())
      self.assertTrue(os.path.exists(revision_filename))
      self.assertEqual(
        revision_filename,
        os.path.join(tempdir, chromium_utils.FULL_BUILD_REVISION_FILENAME),
      )
    finally:
      shutil.rmtree(tempdir)

  def testArchive(self):
    tempdir = tempfile.mkdtemp()
    options = _setup_testdir(tempdir)
    try:
      # Create archive.
      zip_filepath, _ = zip_build.Archive(options)
      self.assertTrue(os.path.exists(zip_filepath))
      # Extract archive.
      unzip_dir = os.path.join(tempdir, 'unzip')
      chromium_utils.ExtractZip(zip_filepath, unzip_dir)
      unzip_entries = os.listdir(unzip_dir)
      self.assertEqual(1, len(unzip_entries))
      package_dir = os.path.join(unzip_dir, unzip_entries[0])
      # Verify that each file in build directory is present in the archive.
      build_dir = _build_dir(options)
      for path, _, files in os.walk(build_dir):
        rel_path = os.path.relpath(path, build_dir)
        for entry in files:
          file_path = os.path.join(package_dir, rel_path, entry)
          self.assertTrue(os.path.exists(file_path))
    finally:
      shutil.rmtree(tempdir)


if __name__ == '__main__':
  unittest.main()
