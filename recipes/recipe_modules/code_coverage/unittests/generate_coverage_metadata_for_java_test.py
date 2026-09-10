#!/usr/bin/env vpython3
# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import os
import sys
import unittest
from xml.etree import ElementTree

import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'resources'))
)

import generate_coverage_metadata_for_java as generator
import repository_util


class GenerateCoverageMetadataForJavaTest(unittest.TestCase):
  INITIAL_METADATA_INPUT = {
    'files': [
      {
        'path': '//dir/file.java',
        'lines': [
          {
            'first': 1,
            'last': 1,
            'count': 1,
          }
        ],
        'summaries': [
          {
            'name': 'line',
            'covered': 1,
            'total': 1,
          }
        ],
      }
    ],
    'summaries': [
      {
        'name': 'line',
        'covered': 1,
        'total': 1,
      }
    ],
  }

  FILE_REVISIONS = {
    '//dir/file.java': ('hash1', 1234),
  }

  COMPONENT_MAPPING = {'dir': 'Test>Component'}

  DIFF_MAPPING = {
    'dir/file.java': {'1': [3, 'A line number changed by the patch.']}
  }

  SOURCEFILE_WITH_COUNTER_INPUT = """
    <sourcefile name="OnDeviceInstrumentationBroker.java">
      <counter type="INSTRUCTION" missed="1" covered="1"/>
      <counter type="BRANCH" missed="1" covered="1"/>
      <counter type="LINE" missed="1" covered="1"/>
      <counter type="COMPLEXITY" missed="1" covered="1"/>
      <counter type="METHOD" missed="1" covered="1"/>
      <counter type="CLASS" missed="1" covered="1"/>
    </sourcefile>
  """

  SOURCEFILE_WITH_COUNTER_INPUT_MISSING_BRANCH = """
    <sourcefile name="OnDeviceInstrumentationBroker.java">
      <counter type="INSTRUCTION" missed="1" covered="1"/>
      <counter type="LINE" missed="1" covered="1"/>
      <counter type="COMPLEXITY" missed="1" covered="1"/>
      <counter type="METHOD" missed="1" covered="1"/>
      <counter type="CLASS" missed="1" covered="1"/>
    </sourcefile>
  """

  JACOCO_REPORT = """
    <report name="JaCoCo Coverage Report">
      <package name="dir">
        <sourcefile name="file.java">
          <line nr="1" mi="0" ci="1" mb="0" cb="0"/>
          <counter type="INSTRUCTION" missed="0" covered="1"/>
          <counter type="LINE" missed="0" covered="1"/>
          <counter type="COMPLEXITY" missed="0" covered="1"/>
          <counter type="METHOD" missed="0" covered="1"/>
          <counter type="CLASS" missed="0" covered="1"/>
        </sourcefile>
      </package>
      <counter type="INSTRUCTION" missed="0" covered="1"/>
      <counter type="LINE" missed="0" covered="1"/>
      <counter type="COMPLEXITY" missed="0" covered="1"/>
      <counter type="METHOD" missed="0" covered="1"/>
      <counter type="CLASS" missed="0" covered="1"/>
    </report>
  """

  JACOCO_REPORT_NO_LINE_INSTRUMENTED = """
    <report name="JaCoCo Coverage Report">
      <package name="dir">
        <sourcefile name="file.java">
          <counter type="INSTRUCTION" missed="0" covered="0"/>
          <counter type="LINE" missed="0" covered="0"/>
          <counter type="COMPLEXITY" missed="0" covered="0"/>
          <counter type="METHOD" missed="0" covered="0"/>
          <counter type="CLASS" missed="0" covered="0"/>
        </sourcefile>
      </package>
      <counter type="INSTRUCTION" missed="0" covered="0"/>
      <counter type="LINE" missed="0" covered="0"/>
      <counter type="COMPLEXITY" missed="0" covered="0"/>
      <counter type="METHOD" missed="0" covered="0"/>
      <counter type="CLASS" missed="0" covered="0"/>
    </report>
  """

  JACOCO_REPORT_WITH_DEFAULT_PACKAGE_NAMES = """
    <report name="Test Report">
      <package name="com/example/package">
        <sourcefile name="Foo.java">
          <line nr="10" ci="1" mi="0" cb="0" mb="0"/>
          <line nr="11" ci="2" mi="0" cb="0" mb="0"/>
          <line nr="12" ci="1" mi="2" cb="2" mb="3"/>
          <line nr="13" ci="0" mi="3" cb="0" mb="0"/>
          <line nr="21" ci="0" mi="4" cb="0" mb="2"/>
          <line nr="22" ci="1" mi="3" cb="0" mb="0"/>
          <counter covered="5" missed="12" type="INSTRUCTION"/>
          <counter covered="2" missed="5" type="BRANCH"/>
          <counter covered="4" missed="2" type="LINE"/>
        </sourcefile>
        <sourcefile name="Bar.java">
          <line nr="1" ci="2" mi="0" cb="0" mb="0"/>
          <line nr="2" ci="1" mi="2" cb="2" mb="3"/>
          <line nr="3" ci="0" mi="3" cb="0" mb="0"/>
        </sourcefile>
      </package>
    </report>
  """

  JACOCO_REPORT_WITH_CORRECT_PACKAGE_NAMES = """
    <report name="Test Report">
      <package name="base/android/com/example/package">
        <sourcefile name="Foo.java">
          <line nr="10" ci="1" mi="0" cb="0" mb="0"/>
          <line nr="11" ci="2" mi="0" cb="0" mb="0"/>
          <line nr="12" ci="1" mi="2" cb="2" mb="3"/>
          <line nr="13" ci="0" mi="3" cb="0" mb="0"/>
          <line nr="21" ci="0" mi="4" cb="0" mb="2"/>
          <line nr="22" ci="1" mi="3" cb="0" mb="0"/>
          <counter covered="5" missed="12" type="INSTRUCTION"/>
          <counter covered="2" missed="5" type="BRANCH"/>
          <counter covered="4" missed="2" type="LINE"/>
        </sourcefile>
      </package>
      <package name = "build/android/com/example/package">
        <sourcefile name="Bar.java">
          <line nr="1" ci="2" mi="0" cb="0" mb="0"/>
          <line nr="2" ci="1" mi="2" cb="2" mb="3"/>
          <line nr="3" ci="0" mi="3" cb="0" mb="0"/>
        </sourcefile>
      </package>
    </report>
  """

  JACOCO_REPORT_WITH_CONTIGUOUS_LINE_NUMBERS = """
    <report name="JaCoCo Coverage Report">
      <package name="dir">
        <sourcefile name="file.java">
          <line nr="1" mi="0" ci="2" mb="0" cb="0"/>
          <line nr="2" mi="0" ci="2" mb="0" cb="0"/>
          <line nr="5" mi="0" ci="1" mb="0" cb="0"/>
          <line nr="6" mi="0" ci="3" mb="0" cb="0"/>
          <line nr="8" mi="0" ci="5" mb="0" cb="0"/>
          <line nr="9" mi="0" ci="5" mb="0" cb="0"/>
          <line nr="10" mi="0" ci="4" mb="0" cb="0"/>
          <counter type="INSTRUCTION" missed="0" covered="1"/>
          <counter type="LINE" missed="0" covered="1"/>
          <counter type="COMPLEXITY" missed="0" covered="1"/>
          <counter type="METHOD" missed="0" covered="1"/>
          <counter type="CLASS" missed="0" covered="1"/>
        </sourcefile>
      </package>
      <counter type="INSTRUCTION" missed="0" covered="1"/>
      <counter type="LINE" missed="0" covered="1"/>
      <counter type="COMPLEXITY" missed="0" covered="1"/>
      <counter type="METHOD" missed="0" covered="1"/>
      <counter type="CLASS" missed="0" covered="1"/>
    </report>
  """

  @mock.patch.object(os, 'walk')
  def test_get_files_with_suffix(self, mock_walk):
    mock_input_dir_walk = [
      ('/b/some/path', ['0', '1', '2', '3'], ['summary.json']),
      (
        '/b/some/path/0',
        [],
        ['output.json', 'default-1.exec', 'default-2.exec'],
      ),
      (
        '/b/some/path/1',
        [],
        ['output.json', 'default-3.exec', 'default-4.exec'],
      ),
    ]
    mock_walk.return_value = mock_input_dir_walk

    expected_output = [
      '/b/some/path/0/default-1.exec',
      '/b/some/path/0/default-2.exec',
      '/b/some/path/1/default-3.exec',
      '/b/some/path/1/default-4.exec',
    ]
    actual_output = generator.get_files_with_suffix('/b/some/path', '.exec')
    self.assertListEqual(expected_output, actual_output)

  @mock.patch.object(os, 'walk')
  def test_get_files_with_suffix_if_there_is_no_match(self, mock_walk):
    mock_input_dir_walk = [
      ('/b/some/path', ['0', '1', '2', '3'], ['summary.json']),
      (
        '/b/some/path/0',
        [],
        ['output.json', 'default-1.exec', 'default-2.exec'],
      ),
      (
        '/b/some/path/1',
        [],
        ['output.json', 'default-3.exec', 'default-4.exec'],
      ),
    ]
    mock_walk.return_value = mock_input_dir_walk

    actual_output = generator.get_files_with_suffix('/b/some/path', '.exe')
    self.assertListEqual([], actual_output)

  def test_get_coverage_metric_summaries(self):
    root = ElementTree.fromstring(self.SOURCEFILE_WITH_COUNTER_INPUT)
    expected_result = [
      {
        'name': 'instruction',
        'covered': 1,
        'total': 2,
      },
      {
        'name': 'branch',
        'covered': 1,
        'total': 2,
      },
      {
        'name': 'line',
        'covered': 1,
        'total': 2,
      },
      {
        'name': 'complexity',
        'covered': 1,
        'total': 2,
      },
      {
        'name': 'method',
        'covered': 1,
        'total': 2,
      },
      {
        'name': 'class',
        'covered': 1,
        'total': 2,
      },
    ]

    actual_result = generator.get_coverage_metric_summaries(root)
    self.assertListEqual(actual_result, expected_result)

  def test_get_coverage_metric_summaries_missing_metric(self):
    root = ElementTree.fromstring(
      self.SOURCEFILE_WITH_COUNTER_INPUT_MISSING_BRANCH
    )
    expected_result = [
      {
        'name': 'instruction',
        'covered': 1,
        'total': 2,
      },
      {
        'name': 'branch',
        'covered': 0,
        'total': 0,
      },
      {
        'name': 'line',
        'covered': 1,
        'total': 2,
      },
      {
        'name': 'complexity',
        'covered': 1,
        'total': 2,
      },
      {
        'name': 'method',
        'covered': 1,
        'total': 2,
      },
      {
        'name': 'class',
        'covered': 1,
        'total': 2,
      },
    ]

    actual_result = generator.get_coverage_metric_summaries(root)
    self.assertListEqual(actual_result, expected_result)

  @mock.patch.object(os.path, 'isfile')
  @mock.patch.object(repository_util, '_GetFileRevisions')
  def test_generate_json_coverage_metadata(
    self, mock_get_file_revisions, mock_os_path_isfile
  ):
    mock_get_file_revisions.return_value = self.FILE_REVISIONS
    mock_os_path_isfile = True
    root = ElementTree.fromstring(self.JACOCO_REPORT)

    expected_output = {
      'files': [
        {
          'branches': [],
          'timestamp': 1234,
          'lines': [{'count': 1, 'last': 1, 'first': 1}],
          'path': '//dir/file.java',
          'summaries': [
            {'covered': 1, 'total': 1, 'name': 'instruction'},
            {'covered': 0, 'total': 0, 'name': 'branch'},
            {'covered': 1, 'total': 1, 'name': 'line'},
            {'covered': 1, 'total': 1, 'name': 'complexity'},
            {'covered': 1, 'total': 1, 'name': 'method'},
            {'covered': 1, 'total': 1, 'name': 'class'},
          ],
          'revision': 'hash1',
        }
      ],
      'dirs': [
        {
          'dirs': [],
          'path': '//dir/',
          'summaries': [
            {'covered': 1, 'total': 1, 'name': 'instruction'},
            {'covered': 0, 'total': 0, 'name': 'branch'},
            {'covered': 1, 'total': 1, 'name': 'line'},
            {'covered': 1, 'total': 1, 'name': 'complexity'},
            {'covered': 1, 'total': 1, 'name': 'method'},
            {'covered': 1, 'total': 1, 'name': 'class'},
          ],
          'files': [
            {
              'path': '//dir/file.java',
              'name': 'file.java',
              'summaries': [
                {'covered': 1, 'total': 1, 'name': 'instruction'},
                {'covered': 0, 'total': 0, 'name': 'branch'},
                {'covered': 1, 'total': 1, 'name': 'line'},
                {'covered': 1, 'total': 1, 'name': 'complexity'},
                {'covered': 1, 'total': 1, 'name': 'method'},
                {'covered': 1, 'total': 1, 'name': 'class'},
              ],
            }
          ],
        },
        {
          'dirs': [
            {
              'path': '//dir/',
              'name': 'dir/',
              'summaries': [
                {'covered': 1, 'total': 1, 'name': 'instruction'},
                {'covered': 0, 'total': 0, 'name': 'branch'},
                {'covered': 1, 'total': 1, 'name': 'line'},
                {'covered': 1, 'total': 1, 'name': 'complexity'},
                {'covered': 1, 'total': 1, 'name': 'method'},
                {'covered': 1, 'total': 1, 'name': 'class'},
              ],
            }
          ],
          'path': '//',
          'summaries': [
            {'covered': 1, 'total': 1, 'name': 'instruction'},
            {'covered': 0, 'total': 0, 'name': 'branch'},
            {'covered': 1, 'total': 1, 'name': 'line'},
            {'covered': 1, 'total': 1, 'name': 'complexity'},
            {'covered': 1, 'total': 1, 'name': 'method'},
            {'covered': 1, 'total': 1, 'name': 'class'},
          ],
          'files': [],
        },
      ],
      'summaries': [
        {'covered': 1, 'total': 1, 'name': 'instruction'},
        {'covered': 0, 'total': 0, 'name': 'branch'},
        {'covered': 1, 'total': 1, 'name': 'line'},
        {'covered': 1, 'total': 1, 'name': 'complexity'},
        {'covered': 1, 'total': 1, 'name': 'method'},
        {'covered': 1, 'total': 1, 'name': 'class'},
      ],
      'components': [
        {
          'dirs': [
            {
              'path': '//dir/',
              'name': 'dir/',
              'summaries': [
                {'covered': 1, 'total': 1, 'name': 'instruction'},
                {'covered': 0, 'total': 0, 'name': 'branch'},
                {'covered': 1, 'total': 1, 'name': 'line'},
                {'covered': 1, 'total': 1, 'name': 'complexity'},
                {'covered': 1, 'total': 1, 'name': 'method'},
                {'covered': 1, 'total': 1, 'name': 'class'},
              ],
            }
          ],
          'path': 'Test>Component',
          'summaries': [
            {'covered': 1, 'total': 1, 'name': 'instruction'},
            {'covered': 0, 'total': 0, 'name': 'branch'},
            {'covered': 1, 'total': 1, 'name': 'line'},
            {'covered': 1, 'total': 1, 'name': 'complexity'},
            {'covered': 1, 'total': 1, 'name': 'method'},
            {'covered': 1, 'total': 1, 'name': 'class'},
          ],
        }
      ],
    }

    actual_output, _ = generator.generate_json_coverage_metadata(
      '', root, self.COMPONENT_MAPPING, None, None
    )
    self.assertDictEqual(expected_output, actual_output)

  @mock.patch.object(os.path, 'isfile')
  @mock.patch.object(repository_util, '_GetFileRevisions')
  def test_generate_json_coverage_metadata_contiguous_line_data(
    self, mock_get_file_revisions, mock_os_path_isfile
  ):
    mock_get_file_revisions.return_value = self.FILE_REVISIONS
    mock_os_path_isfile = True
    root = ElementTree.fromstring(
      self.JACOCO_REPORT_WITH_CONTIGUOUS_LINE_NUMBERS
    )

    expected_output = {
      'files': [
        {
          'branches': [],
          'timestamp': 1234,
          'lines': [
            {'count': 2, 'last': 2, 'first': 1},
            {'count': 1, 'last': 5, 'first': 5},
            {'count': 3, 'last': 6, 'first': 6},
            {'count': 5, 'last': 9, 'first': 8},
            {'count': 4, 'last': 10, 'first': 10},
          ],
          'path': '//dir/file.java',
          'summaries': [
            {'covered': 1, 'total': 1, 'name': 'instruction'},
            {'covered': 0, 'total': 0, 'name': 'branch'},
            {'covered': 1, 'total': 1, 'name': 'line'},
            {'covered': 1, 'total': 1, 'name': 'complexity'},
            {'covered': 1, 'total': 1, 'name': 'method'},
            {'covered': 1, 'total': 1, 'name': 'class'},
          ],
          'revision': 'hash1',
        }
      ],
      'dirs': [
        {
          'dirs': [],
          'path': '//dir/',
          'summaries': [
            {'covered': 1, 'total': 1, 'name': 'instruction'},
            {'covered': 0, 'total': 0, 'name': 'branch'},
            {'covered': 1, 'total': 1, 'name': 'line'},
            {'covered': 1, 'total': 1, 'name': 'complexity'},
            {'covered': 1, 'total': 1, 'name': 'method'},
            {'covered': 1, 'total': 1, 'name': 'class'},
          ],
          'files': [
            {
              'path': '//dir/file.java',
              'name': 'file.java',
              'summaries': [
                {'covered': 1, 'total': 1, 'name': 'instruction'},
                {'covered': 0, 'total': 0, 'name': 'branch'},
                {'covered': 1, 'total': 1, 'name': 'line'},
                {'covered': 1, 'total': 1, 'name': 'complexity'},
                {'covered': 1, 'total': 1, 'name': 'method'},
                {'covered': 1, 'total': 1, 'name': 'class'},
              ],
            }
          ],
        },
        {
          'dirs': [
            {
              'path': '//dir/',
              'name': 'dir/',
              'summaries': [
                {'covered': 1, 'total': 1, 'name': 'instruction'},
                {'covered': 0, 'total': 0, 'name': 'branch'},
                {'covered': 1, 'total': 1, 'name': 'line'},
                {'covered': 1, 'total': 1, 'name': 'complexity'},
                {'covered': 1, 'total': 1, 'name': 'method'},
                {'covered': 1, 'total': 1, 'name': 'class'},
              ],
            }
          ],
          'path': '//',
          'summaries': [
            {'covered': 1, 'total': 1, 'name': 'instruction'},
            {'covered': 0, 'total': 0, 'name': 'branch'},
            {'covered': 1, 'total': 1, 'name': 'line'},
            {'covered': 1, 'total': 1, 'name': 'complexity'},
            {'covered': 1, 'total': 1, 'name': 'method'},
            {'covered': 1, 'total': 1, 'name': 'class'},
          ],
          'files': [],
        },
      ],
      'summaries': [
        {'covered': 1, 'total': 1, 'name': 'instruction'},
        {'covered': 0, 'total': 0, 'name': 'branch'},
        {'covered': 1, 'total': 1, 'name': 'line'},
        {'covered': 1, 'total': 1, 'name': 'complexity'},
        {'covered': 1, 'total': 1, 'name': 'method'},
        {'covered': 1, 'total': 1, 'name': 'class'},
      ],
      'components': [
        {
          'dirs': [
            {
              'path': '//dir/',
              'name': 'dir/',
              'summaries': [
                {'covered': 1, 'total': 1, 'name': 'instruction'},
                {'covered': 0, 'total': 0, 'name': 'branch'},
                {'covered': 1, 'total': 1, 'name': 'line'},
                {'covered': 1, 'total': 1, 'name': 'complexity'},
                {'covered': 1, 'total': 1, 'name': 'method'},
                {'covered': 1, 'total': 1, 'name': 'class'},
              ],
            }
          ],
          'path': 'Test>Component',
          'summaries': [
            {'covered': 1, 'total': 1, 'name': 'instruction'},
            {'covered': 0, 'total': 0, 'name': 'branch'},
            {'covered': 1, 'total': 1, 'name': 'line'},
            {'covered': 1, 'total': 1, 'name': 'complexity'},
            {'covered': 1, 'total': 1, 'name': 'method'},
            {'covered': 1, 'total': 1, 'name': 'class'},
          ],
        }
      ],
    }

    actual_output, _ = generator.generate_json_coverage_metadata(
      '', root, self.COMPONENT_MAPPING, None, None
    )
    self.assertDictEqual(expected_output, actual_output)

  @mock.patch.object(os.path, 'isfile')
  @mock.patch.object(repository_util, '_GetFileRevisions')
  def test_generate_json_coverage_metadata_no_line_instrumented(
    self, mock_get_file_revisions, mock_os_path_isfile
  ):
    mock_get_file_revisions.return_value = self.FILE_REVISIONS
    mock_os_path_isfile = True
    root = ElementTree.fromstring(self.JACOCO_REPORT_NO_LINE_INSTRUMENTED)

    expected_output = {'files': []}

    actual_output, _ = generator.generate_json_coverage_metadata(
      '', root, self.COMPONENT_MAPPING, None, None
    )
    self.assertDictEqual(expected_output, actual_output)

  @mock.patch.object(os.path, 'isfile')
  def test_generate_json_coverage_metadata_for_per_cl(
    self, mock_os_path_isfile
  ):
    mock_os_path_isfile = True
    root = ElementTree.fromstring(self.JACOCO_REPORT)

    expected_output = {
      'files': [
        {
          'path': '//dir/file.java',
          'lines': [{'count': 1, 'last': 3, 'first': 3}],
          'branches': [],
          'summaries': [
            {'covered': 1, 'total': 1, 'name': 'instruction'},
            {'covered': 0, 'total': 0, 'name': 'branch'},
            {'covered': 1, 'total': 1, 'name': 'line'},
            {'covered': 1, 'total': 1, 'name': 'complexity'},
            {'covered': 1, 'total': 1, 'name': 'method'},
            {'covered': 1, 'total': 1, 'name': 'class'},
          ],
        }
      ]
    }

    actual_output, _ = generator.generate_json_coverage_metadata(
      '', root, None, self.DIFF_MAPPING, ['dir/file.java']
    )
    self.assertDictEqual(expected_output, actual_output)

  @mock.patch.object(os.path, 'isfile')
  def test_generate_json_coverage_metadata_for_per_cl_line_removed(
    self, mock_os_path_isfile
  ):
    mock_os_path_isfile = True
    root = ElementTree.fromstring(self.JACOCO_REPORT)
    diff_mapping_line_removed = {'dir/file.java': {}}

    expected_output = {'files': []}

    actual_output, _ = generator.generate_json_coverage_metadata(
      '', root, None, diff_mapping_line_removed, ['dir/file.java']
    )
    self.assertDictEqual(expected_output, actual_output)

  @mock.patch.object(os.path, 'isfile')
  def test_fix_package_paths(self, mock_os_path_isfile):

    def _xml_equal(e1, e2):
      if e1.tag != e2.tag:
        return False
      if e1.attrib != e2.attrib:
        return False
      if len(e1) != len(e2):
        return False
      child_pairs = []
      for child1 in e1:
        for child2 in e2:
          if child1.tag == child2.tag and child1.attrib == child2.attrib:
            child_pairs.append((child1, child2))
      if len(child_pairs) != len(e1):
        return False
      return all(_xml_equal(c1, c2) for c1, c2 in child_pairs)

    isfile_response = {
      'base/android/com/example/package/Foo.java': True,
      'base/android/com/example/package/Bar.java': False,
      'build/android/com/example/package/Foo.java': False,
      'build/android/com/example/package/Bar.java': True,
    }
    mock_os_path_isfile.side_effect = lambda path: isfile_response[path]
    actual_output = generator.fix_package_paths(
      ElementTree.fromstring(self.JACOCO_REPORT_WITH_DEFAULT_PACKAGE_NAMES),
      '',
      ['base/android/com/example/package', 'build/android/com/example/package'],
    )
    self.assertTrue(
      _xml_equal(
        ElementTree.fromstring(self.JACOCO_REPORT_WITH_CORRECT_PACKAGE_NAMES),
        actual_output,
      )
    )


if __name__ == '__main__':
  unittest.main()
