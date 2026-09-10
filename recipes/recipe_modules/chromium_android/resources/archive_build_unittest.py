#!/usr/bin/env python3
# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import os.path
import shutil
import tempfile
import unittest
import zipfile

import archive_build

BINARY_FILES = [
  'test1.apk',
  'test2.apk',
  'lib1.so',
  'lib2.so',
  os.path.join('dir1', 'test3.apk'),
  os.path.join('dir2', 'lib3.so'),
]

INTERMEDIATE_FILES = [
  'test1.o',
  'test2.o',
  'lib1.o',
  'lib2.o',
  os.path.join('dir1', 'test3.o'),
  os.path.join('dir2', 'lib3.o'),
]

SOURCE_FILES = ['a.cpp']


def CreateFileSetInDir(out_dir, file_list):
  for f in file_list:
    dir_part = os.path.dirname(f)
    if dir_part:
      dir_path = os.path.join(out_dir, dir_part)
      if not os.path.exists(dir_path):
        os.makedirs(dir_path)

    temp_file = open(os.path.join(out_dir, f), 'w')
    temp_file.write('contents')
    temp_file.close()


class ArchiveTest(unittest.TestCase):
  def setUp(self):
    self.temp_dir = tempfile.mkdtemp()
    self.zip_file = 'archive.zip'
    self.root_build_dir = os.path.join(self.temp_dir, 'build')
    os.makedirs(self.root_build_dir)
    self.src_dir = os.path.join(self.root_build_dir, 'src')
    os.makedirs(self.src_dir)
    self.out_dir = os.path.join(self.src_dir, 'out')
    os.makedirs(self.out_dir)
    self.build_dir = os.path.join(self.out_dir, 'Debug')
    os.makedirs(self.build_dir)

    # Create test files
    CreateFileSetInDir(self.src_dir, SOURCE_FILES)
    CreateFileSetInDir(self.build_dir, BINARY_FILES)
    CreateFileSetInDir(self.build_dir, INTERMEDIATE_FILES)
    os.chdir(self.src_dir)

  def tearDown(self):
    shutil.rmtree(self.temp_dir)

  def verifyZipFile(self, zip_dir, zip_file_path, expected_files):
    # Extract the files from the archive
    extract_dir = os.path.join(zip_dir, 'extract')
    os.makedirs(extract_dir)
    zip_file = zipfile.ZipFile(zip_file_path)
    if hasattr(zip_file, 'extractall'):
      zip_file.extractall(extract_dir)  # pylint: disable=E1101

      # List of extract_dir-relative files.
      extracted_files = []
      for dirpath, _, filenames in os.walk(extract_dir):
        subdir = dirpath[len(extract_dir) :]
        extracted_files.extend([os.path.join(subdir, f) for f in filenames])
      self.assertCountEqual(expected_files, extracted_files)
    else:
      test_result = zip_file.testzip()
      self.assertTrue(not test_result)
    zip_file.close()

  def testArchiveBuild(self):
    archive_build.archive_build(
      self.build_dir, name=self.zip_file, location='out'
    )
    zip_file_path = os.path.join(self.out_dir, self.zip_file)

    self.assertTrue(os.path.exists(zip_file_path))
    files_list = [
      os.path.join(self.build_dir, x)
      for x in (BINARY_FILES + INTERMEDIATE_FILES)
    ]
    self.verifyZipFile(self.out_dir, zip_file_path, files_list)

  def testArchiveBuildIgnoreSubfolderNames(self):
    archive_build.archive_build(
      self.build_dir,
      name=self.zip_file,
      location='out',
      ignore_subfolder_names=True,
    )
    zip_file_path = os.path.join(self.out_dir, self.zip_file)

    self.assertTrue(os.path.exists(zip_file_path))
    files_list = [
      os.path.basename(x) for x in (BINARY_FILES + INTERMEDIATE_FILES)
    ]
    self.verifyZipFile(self.out_dir, zip_file_path, files_list)

  def testArchiveBuildWithFiles(self):
    files = ['*dir1/test3.o', '../../a.cpp']
    archive_build.archive_build(
      self.build_dir, name=self.zip_file, location='out', files=files
    )
    zip_file_path = os.path.join(self.out_dir, self.zip_file)

    self.assertTrue(os.path.exists(zip_file_path))
    files_list = [
      os.path.join(self.build_dir, 'dir1', 'test3.o'),
      os.path.join(self.build_dir, 'a.cpp'),
    ]
    self.verifyZipFile(self.out_dir, zip_file_path, files_list)

  def testArchiveBuildWithIncludeFilters(self):
    include_filters = ['*.so']
    archive_build.archive_build(
      self.build_dir,
      name=self.zip_file,
      location='out',
      include_filters=include_filters,
    )
    zip_file_path = os.path.join(self.out_dir, self.zip_file)

    self.assertTrue(os.path.exists(zip_file_path))
    out_files = BINARY_FILES + INTERMEDIATE_FILES
    files_list = [
      os.path.join(self.build_dir, x) for x in out_files if x.endswith('.so')
    ]
    self.verifyZipFile(self.out_dir, zip_file_path, files_list)

  def testArchiveBuildWithExcludeFilters(self):
    exclude_filters = ['*.so']
    archive_build.archive_build(
      self.build_dir,
      name=self.zip_file,
      location='out',
      exclude_filters=exclude_filters,
    )
    zip_file_path = os.path.join(self.out_dir, self.zip_file)

    self.assertTrue(os.path.exists(zip_file_path))
    out_files = BINARY_FILES + INTERMEDIATE_FILES
    files_list = [
      os.path.join(self.build_dir, x)
      for x in out_files
      if not x.endswith('.so')
    ]
    self.verifyZipFile(self.out_dir, zip_file_path, files_list)


if __name__ == '__main__':
  suite = unittest.TestLoader().loadTestsFromTestCase(ArchiveTest)
  unittest.TextTestRunner(verbosity=2).run(suite)
