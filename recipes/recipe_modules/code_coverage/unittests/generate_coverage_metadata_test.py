#!/usr/bin/env vpython3
# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json
import os
import sys
import unittest

import mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
  0, os.path.abspath(os.path.join(THIS_DIR, os.pardir, 'resources'))
)

import generate_coverage_metadata as generator


class GenerateCoverageMetadataTest(unittest.TestCase):
  # This test uses the following code:
  # 1|      1|int main() {
  # 2|      1|  if ((2 > 1) || (3 > 2)) {
  # 3|      1|    return 0;
  # 4|      1|  }
  # 5|      0|
  # 6|      0|  return 1;
  # 7|      0|}
  #
  # Where the first column is the line number and the second column is the
  # expected number of times the line is executed.
  def test_parse_exported_coverage_json(self):
    segments = [
      [1, 12, 1, True, True],
      [2, 7, 1, True, True],
      [2, 14, 1, True, False],
      [2, 18, 0, True, True],
      [2, 25, 1, True, False],
      [2, 27, 1, True, True],
      [4, 4, 0, True, False],
      [6, 3, 0, True, True],
      [7, 2, 0, False, False],
    ]

    expected_line_data = dict(
      [(1, 1), (2, 1), (3, 1), (4, 1), (5, 0), (6, 0), (7, 0)]
    )
    expected_block_data = {2: [[18, 24]], 4: [[4, -1]]}
    line_data, block_data = generator._extract_coverage_info(segments)
    self.assertDictEqual(expected_line_data, line_data)
    self.assertDictEqual(expected_block_data, block_data)

  # This test uses the following code:
  # 1|      1|int main() { return 0; }
  #
  # Where the first column is the line number and the second column is the
  # expected number of times the line is executed.
  def test_parse_exported_coverage_json_one_line(self):
    segments = [[1, 12, 1, True, True], [1, 25, 0, False, False]]

    expected_line_data = dict([(1, 1)])
    expected_block_data = {}
    line_data, block_data = generator._extract_coverage_info(segments)
    self.assertDictEqual(expected_line_data, line_data)
    self.assertDictEqual(expected_block_data, block_data)

  # This test uses the following code, and the segments are based on execution
  # on Linux platform.
  #
  #  1|       |#include "testing/gtest/include/gtest/gtest.h"
  #  2|       |
  #  3|      0|TEST(GUIDTest, DISABLED_GUIDGeneratesAllZeroes) {
  #  4|      0|
  #  5|       |  #if defined(OS_ANDROID) || defined(OS_CHROMEOS)
  #  6|       |    EXPECT_EQ(1, 1);
  #  7|       |  #else
  #  8|      0|    EXPECT_EQ(2, 2);
  #  9|      0|  #endif
  # 10|      0|}
  #
  # Where the first column is the line number and the second column is the
  # expected number of times the line is executed.
  def test_parse_exported_coverage_json_uncovered_macros(self):
    segments = [
      [3, 49, 0, True, True],
      [5, 3, 0, False, True],
      [8, 1, 0, True, False],
      [8, 5, 0, True, True],
      [8, 14, 0, True, False],
      [10, 2, 0, False, False],
    ]

    expected_line_data = dict([(3, 0), (4, 0), (8, 0), (9, 0), (10, 0)])
    expected_block_data = {}
    line_data, block_data = generator._extract_coverage_info(segments)
    self.assertDictEqual(expected_line_data, line_data)
    self.assertDictEqual(expected_block_data, block_data)

  # This test uses the following code:
  # 1|      1|int main() {
  # 2|      1|  if ((2 > 1) || (3 > 2)) {
  # 3|      1|    return 0;
  # 4|      1|  }
  # 5|      0|
  # 6|      0|  return 1;
  # 7|      0|}
  #
  # Where the first column is the line number and the second column is the
  # expected number of times the line is executed.
  def test_to_compressed_file_record(self):
    file_coverage_data = {
      'segments': [
        [1, 12, 1, True, True],
        [2, 7, 1, True, True],
        [2, 14, 1, True, False],
        [2, 18, 0, True, True],
        [2, 25, 1, True, False],
        [2, 27, 1, True, True],
        [4, 4, 0, True, False],
        [6, 3, 0, True, True],
        [7, 2, 0, False, False],
      ],
      'summary': {
        'lines': {
          'covered': 4,
          'count': 7,
        }
      },
      'filename': 'base/base.cc',
    }
    expected_record = {
      'path': '//base/base.cc',
      'summaries': [
        {
          'covered': 4,
          'name': 'line',
          'total': 7,
        }
      ],
      'lines': [
        {
          'first': 1,
          'last': 4,
          'count': 1,
        },
        {
          'first': 5,
          'last': 7,
          'count': 0,
        },
      ],
      'uncovered_blocks': [
        {
          'line': 2,
          'ranges': [
            {
              'first': 18,
              'last': 24,
            }
          ],
        },
        {
          'line': 4,
          'ranges': [
            {
              'first': 4,
              'last': -1,
            }
          ],
        },
      ],
    }
    self.maxDiff = None
    record = generator._to_compressed_file_record(file_coverage_data)
    self.assertDictEqual(expected_record, record)

  # This test uses made-up segments, and the intention is to test that for
  # *uncontinous* regions, even if their lines are executed the same number of
  # times, when converted to compressed format, lines in different regions
  # shouldn't be merged together.
  def test_to_compressed_file_record_for_uncontinous_lines(self):
    file_coverage_data = {
      'segments': [
        [102, 35, 4, True, True],
        [104, 4, 0, False, False],
        [107, 35, 4, True, True],
        [109, 4, 0, False, False],
      ],
      'summary': {
        'lines': {
          'covered': 6,
          'count': 6,
        }
      },
      'filename': 'base/base.cc',
    }
    expected_record = {
      'path': '//base/base.cc',
      'summaries': [
        {
          'covered': 6,
          'name': 'line',
          'total': 6,
        }
      ],
      'lines': [
        {
          'first': 102,
          'last': 104,
          'count': 4,
        },
        {
          'first': 107,
          'last': 109,
          'count': 4,
        },
      ],
    }
    self.maxDiff = None
    record = generator._to_compressed_file_record(file_coverage_data)
    self.assertDictEqual(expected_record, record)

  # This test uses the following code:
  # 1|      1|int main() {
  # 2|      1|  if ((2 > 1) || (3 > 2)) {
  # 3|      1|    return 0;
  # 4|      1|  }
  # 5|      0|
  # 6|      0|  return 1;
  # 7|      0|}
  #
  # Where the first column is the line number and the second column is the
  # expected number of times the line is executed.
  def test_to_compressed_file_record_with_diff_mapping(self):
    file_coverage_data = {
      'segments': [
        [1, 12, 1, True, True],
        [2, 7, 1, True, True],
        [2, 14, 1, True, False],
        [2, 18, 0, True, True],
        [2, 25, 1, True, False],
        [2, 27, 1, True, True],
        [4, 4, 0, True, False],
        [6, 3, 0, True, True],
        [7, 2, 0, False, False],
      ],
      'summary': {
        'lines': {
          'covered': 2,
          'count': 7,
        }
      },
      'filename': 'base/base.cc',
    }
    diff_mapping = {
      'base/base.cc': {
        '2': [10, 'A line added by the patch.'],
        '3': [11, 'Another added line.'],
        '5': [16, 'One more line.'],
      }
    }

    record = generator._to_compressed_file_record(
      file_coverage_data, diff_mapping
    )

    expected_record = {
      'path': '//base/base.cc',
      'summaries': [
        {
          'covered': 2,
          'name': 'line',
          'total': 7,
        }
      ],
      'lines': [
        {
          'first': 10,
          'last': 11,
          'count': 1,
        },
        {
          'first': 16,
          'last': 16,
          'count': 0,
        },
      ],
      'uncovered_blocks': [
        {
          'line': 10,
          'ranges': [
            {
              'first': 18,
              'last': 24,
            }
          ],
        }
      ],
    }

    self.maxDiff = None
    self.assertDictEqual(expected_record, record)

  # This test uses the following code:
  # 1|      1|int main() {
  # 2|      1|  if ((2 > 1) || (3 > 2)) {
  # 3|      1|    return 0;
  # 4|      1|  }
  # 5|      0|
  # 6|      0|  return 1;
  # 7|      0|}
  #
  # Where the first column is the line number and the second column is the
  # expected number of times the line is executed.
  def test_cleanup_coverage_data(self):
    src_path = '/path/to/chromium/src'
    raw_data = {
      'data': [
        {
          'files': [
            {
              'segments': [
                [1, 12, 1, True, True],
                [2, 7, 1, True, True],
                [2, 14, 1, True, False],
                [2, 18, 0, True, True],
                [2, 25, 1, True, False],
                [2, 27, 1, True, True],
                [4, 4, 0, True, False],
                [6, 3, 0, True, True],
                [7, 2, 0, False, False],
              ],
              'summary': {
                'lines': {
                  'covered': 2,
                  'count': 7,
                }
              },
              'filename': '/path/to/chromium/src/base/base.cc',
            }
          ]
        }
      ],
      'type': 'llvm.coverage.json.export',
      'version': '2.0.1',
    }
    expected_cleaned_data = {
      'data': [
        {
          'files': [
            {
              'segments': [
                [1, 12, 1, True, True],
                [2, 7, 1, True, True],
                [2, 14, 1, True, False],
                [2, 18, 0, True, True],
                [2, 25, 1, True, False],
                [2, 27, 1, True, True],
                [4, 4, 0, True, False],
                [6, 3, 0, True, True],
                [7, 2, 0, False, False],
              ],
              'summary': {
                'lines': {
                  'covered': 2,
                  'count': 7,
                }
              },
              'filename': 'base/base.cc',
            }
          ]
        }
      ],
      'type': 'llvm.coverage.json.export',
      'version': '2.0.1',
    }

    cleaned_data = generator._cleanup_coverage_data(src_path, raw_data)
    self.maxDiff = None
    self.assertDictEqual(expected_cleaned_data, cleaned_data)

  def test_split_llvm_data_in_shards(self):
    data = {
      'data': [
        {
          'files': [
            {
              'segments': [
                [1, 12, 1, True, True],
              ],
              'summary': {
                'lines': {
                  'covered': 1,
                  'count': 1,
                }
              },
              'filename': '/path/to/chromium/src/base/base1.cc',
            },
            {
              'segments': [
                [1, 12, 1, True, True],
              ],
              'summary': {
                'lines': {
                  'covered': 1,
                  'count': 1,
                }
              },
              'filename': '/path/to/chromium/src/base/base2.cc',
            },
          ]
        }
      ],
      'type': 'llvm.coverage.json.export',
      'version': '2.0.1',
    }
    expected_sharded_data = [
      {
        'data': [
          {
            'files': [
              {
                'segments': [
                  [1, 12, 1, True, True],
                ],
                'summary': {
                  'lines': {
                    'covered': 1,
                    'count': 1,
                  }
                },
                'filename': '/path/to/chromium/src/base/base1.cc',
              }
            ]
          }
        ],
        'type': 'llvm.coverage.json.export',
        'version': '2.0.1',
      },
      {
        'data': [
          {
            'files': [
              {
                'segments': [
                  [1, 12, 1, True, True],
                ],
                'summary': {
                  'lines': {
                    'covered': 1,
                    'count': 1,
                  }
                },
                'filename': '/path/to/chromium/src/base/base2.cc',
              }
            ]
          }
        ],
        'type': 'llvm.coverage.json.export',
        'version': '2.0.1',
      },
    ]
    actual_sharded_data = generator._split_llvm_data_in_shards(
      data, shard_size=1
    )

    self.assertEqual(expected_sharded_data, actual_sharded_data)

  # This test uses the following code:
  # 1|      1|int main() {
  # 2|      1|  if ((2 > 1) || (3 > 2)) {
  # 3|      1|    return 0;
  # 4|      1|  }
  # 5|      0|
  # 6|      0|  return 1;
  # 7|      0|}
  #
  # Where the first column is the line number and the second column is the
  # expected number of times the line is executed.
  def test_rebase_line_and_block_data(self):
    line_data = {1: 1, 2: 1, 3: 1, 4: 1, 5: 0, 6: 0, 7: 0}
    block_data = {2: [[18, 24]]}
    file_name = 'base/base.cc'
    diff_mapping = {'base/base.cc': {'2': [16, 'A line added by the patch.']}}

    rebased_line_data, rebased_block_data = (
      generator._rebase_line_and_block_data(
        line_data, block_data, diff_mapping[file_name]
      )
    )

    expected_line_data = {16: 1}
    expected_block_data = {16: [[18, 24]]}
    self.maxDiff = None
    self.assertDictEqual(expected_line_data, rebased_line_data)
    self.assertDictEqual(expected_block_data, rebased_block_data)

  def test_compute_llvm_args(self):
    args = generator._compute_llvm_args(
      '/path/to/coverage.profdata',
      '/path/to/llvm-cov',
      '/path/to/build_dir',
      ['/path/to/1.exe', '/path/to/2.exe'],
      ['/src/a.cc', '/src/b.cc'],
      1,
      arch="x86_64",
    )
    expected_args = [
      '/path/to/llvm-cov',
      'export',
      '-skip-expansions',
      '-skip-functions',
      '-num-threads',
      '1',
      '-compilation-dir',
      '/path/to/build_dir',
      '-arch=x86_64',
      '-arch=x86_64',
      '-instr-profile',
      '/path/to/coverage.profdata',
      '/path/to/1.exe',
      '-object',
      '/path/to/2.exe',
      '/src/a.cc',
      '/src/b.cc',
    ]
    self.assertListEqual(expected_args, args)

  # This test uses the following code:
  # 1|      1|int main() {
  # 2|      1|  if ((2 > 1) || (3 > 2)) {
  # 3|      1|    return 0;
  # 4|      1|  }
  # 5|      0|
  # 6|      0|  return 1;
  # 7|      0|}
  #
  # Where the first column is the line number and the second column is the
  # expected number of times the line is executed.
  @mock.patch.object(generator, '_write_coverage_to_disk')
  @mock.patch.object(generator, '_get_per_target_coverage_summary')
  @mock.patch.object(generator, '_get_raw_coverage_data')
  def test_generate_metadata_for_per_cl_coverage(
    self, mock_get_coverage_data, mock_get_per_target_coverage_summary, *_
  ):
    mock_get_coverage_data.return_value = {
      'data': [
        {
          'files': [
            {
              'segments': [
                [1, 12, 1, True, True],
                [2, 7, 1, True, True],
                [2, 14, 1, True, False],
                [2, 18, 0, True, True],
                [2, 25, 1, True, False],
                [2, 27, 1, True, True],
                [4, 4, 0, True, False],
                [6, 3, 0, True, True],
                [7, 2, 0, False, False],
              ],
              'summary': {
                'lines': {'count': 7, 'covered': 4, 'percent': 57},
              },
              'filename': '/path/to/src/dir/file.cc',
            }
          ]
        }
      ],
      'type': 'llvm.coverage.json.export',
      'version': '2.0.1',
    }

    # We don't care about the summaries for this test.
    mock_get_per_target_coverage_summary.return_value = {}

    diff_mapping = {
      'dir/file.cc': {
        '2': [10, 'A line added by the patch.'],
        '3': [11, 'Another added line.'],
        '5': [16, 'One more line.'],
      }
    }

    compressed_data, _ = generator._generate_metadata(
      src_path='/path/to/src',
      output_dir='/path/to/output_dir',
      profdata_path='/path/to/coverage.profdata',
      llvm_cov_path='/path/to/llvm-cov',
      build_dir='/path/to/build_dir',
      binaries=['/path/to/binary1', '/path/to/binary2'],
      component_mapping=None,
      sources=['/path/to/src/dir/file.cc'],
      diff_mapping=diff_mapping,
    )

    expected_compressed_files = [
      {
        'path': '//dir/file.cc',
        'lines': [
          {
            'count': 1,
            'last': 11,
            'first': 10,
          },
          {
            'count': 0,
            'last': 16,
            'first': 16,
          },
        ],
        'summaries': [
          {
            'covered': 4,
            'name': 'line',
            'total': 7,
          }
        ],
        'uncovered_blocks': [
          {
            'ranges': [
              {
                'last': 24,
                'first': 18,
              }
            ],
            'line': 10,
          }
        ],
      }
    ]
    self.maxDiff = None
    self.assertListEqual(expected_compressed_files, compressed_data['files'])

  # This test uses the following code:
  # /path/to/src/dir1/file1.cc
  # 1|      1|int main() {
  # 2|      1|  if ((2 > 1) || (3 > 2)) {
  # 3|      1|    return 0;
  # 4|      1|  }
  # 5|      0|
  # 6|      0|  return 1;
  # 7|      0|}
  #
  # Where the first column is the line number and the second column is the
  # expected number of times the line is executed.

  @mock.patch.object(generator, '_write_coverage_to_disk')
  @mock.patch.object(generator, '_get_per_target_coverage_summary')
  @mock.patch.object(generator.repository_util, '_GetFileRevisions')
  @mock.patch.object(generator, '_get_raw_coverage_data')
  def test_generate_metadata_for_full_repo_coverage(
    self,
    mock_get_coverage_data,
    mock__GetFileRevisions,
    mock_get_per_target_coverage_summary,
    *_,
  ):
    # Number of files should not exceed 1000; otherwise sharding will happen.
    mock__GetFileRevisions.return_value = {
      '//dir1/file1.cc': ('hash1', 1234),
    }
    mock_get_coverage_data.return_value = {
      'data': [
        {
          'files': [
            {
              'segments': [
                [1, 12, 1, True, True],
                [2, 7, 1, True, True],
                [2, 14, 1, True, False],
                [2, 18, 0, True, True],
                [2, 25, 1, True, False],
                [2, 27, 1, True, True],
                [4, 4, 0, True, False],
                [6, 3, 0, True, True],
                [7, 2, 0, False, False],
              ],
              'summary': {
                'lines': {'count': 7, 'covered': 4, 'percent': 57},
              },
              'filename': '/path/to/src/dir1/file1.cc',
            },
          ]
        }
      ],
      'type': 'llvm.coverage.json.export',
      'version': '2.0.1',
    }

    # We don't care about the summaries for this test.
    mock_get_per_target_coverage_summary.return_value = {}

    component_mapping = {'dir1': 'Test>Component'}

    compressed_data, _ = generator._generate_metadata(
      src_path='/path/to/src',
      output_dir='/path/to/output_dir',
      profdata_path='/path/to/coverage.profdata',
      llvm_cov_path='/path/to/llvm-cov',
      build_dir='/path/to/build_dir',
      binaries=['/path/to/binary1', '/path/to/binary2'],
      component_mapping=component_mapping,
      sources=[],
      exclusions='.*bad_file.*',
    )

    expected_compressed_components = [
      {
        'dirs': [
          {
            'path': '//dir1/',
            'name': 'dir1/',
            'summaries': [
              {
                'covered': 4,
                'total': 7,
                'name': 'line',
              }
            ],
          }
        ],
        'path': 'Test>Component',
        'summaries': [
          {
            'covered': 4,
            'total': 7,
            'name': 'line',
          }
        ],
      }
    ]

    self.maxDiff = None
    self.assertListEqual(
      expected_compressed_components, compressed_data['components']
    )

    expected_compressed_summaries = [{'covered': 4, 'total': 7, 'name': 'line'}]

    self.assertListEqual(
      expected_compressed_summaries, compressed_data['summaries']
    )

    expected_compressed_dirs = [
      {
        'dirs': [],
        'files': [
          {
            'name': 'file1.cc',
            'path': '//dir1/file1.cc',
            'summaries': [
              {
                'covered': 4,
                'total': 7,
                'name': 'line',
              }
            ],
          }
        ],
        'summaries': [
          {
            'covered': 4,
            'total': 7,
            'name': 'line',
          }
        ],
        'path': '//dir1/',
      },
      {
        'dirs': [
          {
            'path': '//dir1/',
            'name': 'dir1/',
            'summaries': [
              {
                'covered': 4,
                'total': 7,
                'name': 'line',
              }
            ],
          }
        ],
        'files': [],
        'summaries': [{'covered': 4, 'total': 7, 'name': 'line'}],
        'path': '//',
      },
    ]

    self.assertListEqual(expected_compressed_dirs, compressed_data['dirs'])

    expected_compressed_files = [
      {
        'path': '//dir1/file1.cc',
        'lines': [
          {
            'count': 1,
            'last': 4,
            'first': 1,
          },
          {
            'count': 0,
            'last': 7,
            'first': 5,
          },
        ],
        'summaries': [
          {
            'covered': 4,
            'name': 'line',
            'total': 7,
          }
        ],
        'uncovered_blocks': [
          {
            'ranges': [
              {
                'last': 24,
                'first': 18,
              }
            ],
            'line': 2,
          },
          {
            'ranges': [
              {
                'last': -1,
                'first': 4,
              }
            ],
            'line': 4,
          },
        ],
        'revision': 'hash1',
        'timestamp': 1234,
      },
    ]

    self.assertListEqual(expected_compressed_files, compressed_data['files'])

  @mock.patch('psutil.cpu_count')
  @mock.patch('subprocess.check_output')
  def test_per_target_summaries(self, call, cpu_count):
    summary_data = {
      'data': [
        {
          'totals': {
            'functions': {'count': 1, 'covered': 1, 'percent': 100},
            'instantiations': {'count': 1, 'covered': 1, 'percent': 100},
            'lines': {'count': 3, 'covered': 3, 'percent': 100},
            'regions': {
              'count': 1,
              'covered': 1,
              'notcovered': 0,
              'percent': 100,
            },
          }
        }
      ]
    }
    call.return_value = json.dumps(summary_data)
    # llvm-cov should use (cpu_count - 5) threads
    cpu_count.return_value = 100

    summaries = generator._get_per_target_coverage_summary(
      '/foo/bar/baz.profdata',
      '/path/to/llvm-cov',
      '/path/to/build_dir',
      ['binary1'],
      arch=None,
    )

    call.assert_called_with(
      [
        '/path/to/llvm-cov',
        'export',
        '-skip-expansions',
        '-skip-functions',
        '-num-threads',
        '95',
        '-compilation-dir',
        '/path/to/build_dir',
        '-summary-only',
        '-instr-profile',
        '/foo/bar/baz.profdata',
        'binary1',
      ],
      text=True,
    )
    self.assertIn('binary1', summaries)
    self.assertEqual(summaries['binary1'], summary_data['data'][0]['totals'])

  @mock.patch('psutil.Process')
  def test_exception_in_show_system_resource_usage(self, mock_process):
    psutil_process = mock_process.return_value

    def side_effect_exception():
      raise ValueError('ValueError in mock')

    psutil_process.num_threads.side_effect = side_effect_exception

    # Expection raised from num_threads() is caught and not further raised.
    generator._show_system_resource_usage(psutil_process)


if __name__ == '__main__':
  unittest.main()
