#!/usr/bin/env vpython3
# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import sys
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'resources'))
)

import diff_util


class DiffUtilTest(unittest.TestCase):
  def test_generate_line_number_mapping_same_file(self):
    diff_lines = []
    from_file_lines = ['The same content']
    to_file_lines = ['The same content']

    line_num_mapping = diff_util.generate_line_number_mapping(
      diff_lines, from_file_lines, to_file_lines
    )
    self.assertDictEqual({1: (1, 'The same content')}, line_num_mapping)

  def test_generate_line_number_mapping_one_diff_section(self):
    diff_lines = [
      'diff --git a/a.txt b/b.txt',
      'index a92d664..d147165 100644',
      '--- a/a.txt',
      '+++ b/b.txt',
      '@@ -1,3 +1,2 @@',
      '-line 1',
      '-line 2',
      '+line 2, changed',
      ' line 3',
    ]
    from_file_lines = ['line 1', 'line 2', 'line 3']
    to_file_lines = ['line 2, changed', 'line 3']

    line_num_mapping = diff_util.generate_line_number_mapping(
      diff_lines, from_file_lines, to_file_lines
    )
    self.assertDictEqual({3: (2, 'line 3')}, line_num_mapping)

  def test_generate_line_number_mapping_multiple_diff_section(self):
    diff_lines = [
      'diff --git a/a.txt b/b.txt',
      'index 6472a22..11579c6 100644',
      '--- a/a.txt',
      '+++ b/b.txt',
      '@@ -1,6 +1,5 @@',
      ' line 1',
      '-line 2',
      '-line 3',
      '+line 3, changed',
      ' line 4',
      ' line 5',
      ' line 6',
      '@@ -11,5 +10,5 @@ line 10',
      ' line 11',
      ' line 12',
      ' line 13',
      '-line 14',
      '+line 14, changed',
      ' line 15',
    ]
    from_file_lines = [
      'line 1',
      'line 2',
      'line 3',
      'line 4',
      'line 5',
      'line 6',
      'line 7',
      'line 8',
      'line 9',
      'line 10',
      'line 11',
      'line 12',
      'line 13',
      'line 14',
      'line 15',
    ]
    to_file_lines = [
      'line 1',
      'line 3, changed',  # line 2 removed and line 3 changed.
      'line 4',
      'line 5',
      'line 6',
      'line 7',
      'line 8',
      'line 9',
      'line 10',
      'line 11',
      'line 12',
      'line 13',
      'line 14, changed',  # line 14 changed.
      'line 15',
    ]

    line_num_mapping = diff_util.generate_line_number_mapping(
      diff_lines, from_file_lines, to_file_lines
    )
    self.assertDictEqual(
      {
        1: (1, 'line 1'),
        4: (3, 'line 4'),
        5: (4, 'line 5'),
        6: (5, 'line 6'),
        7: (6, 'line 7'),
        8: (7, 'line 8'),
        9: (8, 'line 9'),
        10: (9, 'line 10'),
        11: (10, 'line 11'),
        12: (11, 'line 12'),
        13: (12, 'line 13'),
        15: (14, 'line 15'),
      },
      line_num_mapping,
    )

  def test_added_lines_of_one_file_one_diff_section(self):
    diff_lines = [
      'diff --git a.txt b.txt',
      'index 0719398930..4a2b716881 100644',
      '--- a/path/test.txt',
      '+++ b/path/test.txt',
      '@@ -10,2 +10,3 @@',
      ' Line 10',
      '-Line 11',
      '+A different line 11',
      '+A newly added line 12',
    ]

    expected_result = {'path/test.txt': set([11, 12])}
    result = diff_util.parse_added_line_num_from_unified_diff(diff_lines)
    self.assertDictEqual(expected_result, result)

  def test_added_lines_of_one_file_multiple_diff_section(self):
    diff_lines = [
      'diff --git a/path/test.txt b/path/test.txt',
      'index 0719398930..4a2b716881 100644',
      '--- a/path/test.txt',
      '+++ b/path/test.txt',
      '@@ -10,2 +10,3 @@',
      ' Line 10',
      '-Line 11',
      '+A different line 11',
      '+A newly added line 12',
      '@@ -20,1 +21,1 @@',
      '-Line 20',
      '+A different line 21',
    ]

    expected_result = {'path/test.txt': set([11, 12, 21])}
    result = diff_util.parse_added_line_num_from_unified_diff(diff_lines)
    self.assertDictEqual(expected_result, result)

  def test_added_lines_of_multiple_file_multiple_diff_section(self):
    diff_lines = [
      'diff --git a/path/test1.txt b/path/test1.txt',
      'index 0719398930..4a2b716881 100644',
      '--- a/path/test1.txt',
      '+++ b/path/test1.txt',
      '@@ -10,2 +10,3 @@',
      ' Line 10',
      '-Line 11',
      '+A different line 11',
      '+A newly added line 12',
      'diff --git a/path/test2.txt b/path/test2.txt',
      'index 0719398930..4a2b716881 100644',
      '--- a/path/test2.txt',
      '+++ b/path/test2.txt',
      '@@ -10,2 +10,3 @@',
      ' Line 10',
      '-Line 11',
      '+A different line 11',
      '+A newly added line 12',
      '@@ -20,1 +21,1 @@',
      '-Line 20',
      '+A different line 21',
    ]

    expected_result = {
      'path/test1.txt': set([11, 12]),
      'path/test2.txt': set([11, 12, 21]),
    }
    result = diff_util.parse_added_line_num_from_unified_diff(diff_lines)
    self.assertEqual(expected_result, result)

  # This test tests that the calculating added lines correctly handles newlines
  # in all following 3 scerios:
  # 1. If a newline is added, the diff is: '+',.
  # 2. If a newline is unchanged, the diff is: '',. (without prefix whitespace)
  # 3. If a newline is deleted, the diff is: '-',.
  def test_added_lines_new_line_behaviors(self):
    diff_lines = [
      'diff --git a/path/test.txt b/path/test.txt',
      'index 0719398930..4a2b716881 100644',
      '--- a/path/test.txt',
      '+++ b/path/test.txt',
      '@@ -10,3 +10,4 @@',
      ' Line 10',
      '',
      '-',
      '+A different line 11',
      '+',
    ]

    expected_result = {
      'path/test.txt': set([12, 13]),
    }
    result = diff_util.parse_added_line_num_from_unified_diff(diff_lines)
    self.assertEqual(expected_result, result)


if __name__ == '__main__':
  unittest.main()
