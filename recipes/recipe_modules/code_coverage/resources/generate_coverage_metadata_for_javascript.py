# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Script to generate JavaScript coverage metadata file.

The code coverage data format is defined at:
https://chromium.googlesource.com/infra/infra/+/refs/heads/main/appengine/findit/model/proto/code_coverage.proto
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
import sys
import zlib

import aggregation_util
import repository_util

# LCOV format keywords
SF_MARKER = "SF:"
DA_MARKER = "DA:"
END_OF_RECORD_MARKER = "end_of_record"

COVERAGE_FILE_NAME = 'lcov.info'


def _get_line_coverage_metric_summary(lines):
  """Get line coverage summary metric for supplied lines

  Args:
    lines: A list of LineRange's which are sourced from the
      `_to_compressed_format` method.

  Returns:
    A dictionary containing the summary of line coverage
    which is defined as:
    {
      name: Hardcoded to 'line'.
      total: Number of instrumented lines in the file
        which should always be the number of lines in
        JavaScript coverage.
      covered: Number of lines with invocation count >0.
    }
  """
  line_coverage_metric = {}
  line_coverage_metric['name'] = 'line'
  line_coverage_metric['total'] = 0
  line_coverage_metric['covered'] = 0
  for line_range in lines:
    # First and last are inclusive line numbers.
    lines_in_range = line_range['last'] - line_range['first'] + 1
    if line_range['count'] > 0:
      line_coverage_metric['covered'] += lines_in_range
    line_coverage_metric['total'] += lines_in_range
  return line_coverage_metric


def _to_compressed_format(line_data):
  """Compresses execution count dict to a compressed format.

  Args:
    line_data(dict): A mapping from line number to execution count.
  """
  line_data = sorted(list(line_data.items()), key=lambda x: x[0])
  lines = []
  # Aggregate contiguous blocks of lines with the exact same hit count.
  last_index = 0
  for i in range(1, len(line_data) + 1):
    is_continous_line = (
      i < len(line_data) and line_data[i][0] == line_data[i - 1][0] + 1
    )
    has_same_count = (
      i < len(line_data) and line_data[i][1] == line_data[i - 1][1]
    )
    # Merge two lines iff they have continous line number and exactly the same
    # count. For example: (101, 10) and (102, 10).
    if is_continous_line and has_same_count:
      continue
    lines.append(
      {
        'first': line_data[last_index][0],
        'last': line_data[i - 1][0],
        'count': line_data[last_index][1],
      }
    )
    last_index = i
  return lines


def _rebase_exec_count(exec_count, line_mapping):
  rebased_exec_count = {}
  for line_num, count in exec_count.items():
    if str(line_num) not in line_mapping:
      continue
    rebased_line_num = line_mapping[str(line_num)][0]
    rebased_exec_count[rebased_line_num] = count
  return rebased_exec_count


def _to_compressed_file_record(lcov_lines, sources=None, diff_mapping=None):
  """Converts the given JS file coverage data to coverage metadata format.

  Coverage metadata format:
  https://chromium.googlesource.com/infra/infra/+/refs/heads/main/appengine/findit/model/proto/code_coverage.proto.

  This method currenly generates line coverage only. Branch and function coverage is ignored
  """
  path = ''
  exec_count = {}
  all_files_data = []
  for line in lcov_lines:
    if line.startswith(SF_MARKER):
      # SF:<path to source file name>
      assert not path, "Unexpected new SF line %s" % line
      assert not exec_count, "Unexpected exec_count for SF line %s" % line
      path = line.lstrip(SF_MARKER).strip()
    elif line.startswith(DA_MARKER):
      # DA:<line number>,<execution count>[,<checksum>]
      assert path, "Unexpected new DA line %s" % line
      parts = line.split(",")
      line_number = int(parts[0].lstrip(DA_MARKER))
      execution_count = int(parts[1])
      assert line_number > 0, "Invalid line number in DA line %s" % line
      assert line_number not in exec_count, (
        "Unexpected line number in DA line %s" % line
      )
      exec_count[line_number] = execution_count
    elif line.startswith(END_OF_RECORD_MARKER):
      if sources and path not in sources:
        path = ''
        exec_count = {}
        continue
      if diff_mapping is not None and path in diff_mapping:
        line_mapping = diff_mapping[path]
        exec_count = _rebase_exec_count(exec_count, line_mapping)
      lines = _to_compressed_format(exec_count)
      data = {
        'path': '//' + path,
        'lines': lines,
        'summaries': [_get_line_coverage_metric_summary(lines)],
      }
      all_files_data.append(data)
      path = ''
      exec_count = {}
  return all_files_data


def _get_raw_coverage_data(coverage_file):
  """Reads raw coverage data from disk"""
  with open(coverage_file) as f:
    coverage_data = f.readlines()
  return coverage_data


def generate_json_coverage_metadata(
  coverage_dir, src_path, component_mapping, sources=None, diff_mapping=None
):
  """Generate a JSON output representing JavaScript code coverage.

  JSON format conforms to the proto:
  //infra/appengine/findit/model/proto/code_coverage.proto

  Args:
    coverage_dir: Absolute path that contains merged v8 coverage reports.
    src_path: The absolute path to the source files checkout.
    component_mapping: Mapping of monorail component to directory.

  Returns:
    JSON format coverage metadata.
  """
  data = {}
  coverage_file = '%s/%s' % (coverage_dir, COVERAGE_FILE_NAME)
  raw_data = _get_raw_coverage_data(coverage_file)
  data['files'] = _to_compressed_file_record(raw_data, sources, diff_mapping)
  if not data['files']:
    raise Exception('No coverage data associated with source files found.')
  # Add git revision and timestamp per source file.
  repository_util.AddGitRevisionsToCoverageFilesMetadata(
    data['files'], src_path, 'DEPS'
  )
  logging.info('Adding directories and components coverage data ...')
  per_directory_coverage_data, per_component_coverage_data = (
    aggregation_util.get_aggregated_coverage_data_from_files(
      data['files'], component_mapping
    )
  )

  data['components'] = None
  data['dirs'] = None
  if per_component_coverage_data:
    data['components'] = list(per_component_coverage_data.values())
  if per_directory_coverage_data:
    data['dirs'] = list(per_directory_coverage_data.values())
    data['summaries'] = per_directory_coverage_data['//']['summaries']

  return data


def _parse_args(args):
  """Parses the arguments.

  Args:
    args: The passed arguments.

  Returns:
    The parsed arguments as parameters.
  """
  parser = argparse.ArgumentParser(
    description='Generate the JavaScript coverage metadata'
  )
  parser.add_argument(
    '--src-path',
    required=True,
    type=str,
    help='absolute path to the code checkout',
  )
  parser.add_argument(
    '--output-dir',
    required=True,
    type=str,
    help='absolute path to the directory to write the metadata, must exist',
  )
  parser.add_argument(
    '--coverage-dir',
    required=True,
    type=str,
    help='absolute path to the directory that contains merged JavaScript '
    'coverage data',
  )
  parser.add_argument(
    '--dir-metadata-path',
    type=str,
    help='absolute path to json file mapping dirs to metadata',
  )
  parser.add_argument(
    '--source-files',
    nargs='*',
    type=str,
    help='a list of source files to generate coverage data for.'
    'path should be relative to the root of the code checkout.',
  )
  parser.add_argument(
    '--diff-mapping-path',
    type=str,
    help='absolute path to the file that stores the diff mapping',
  )
  params = parser.parse_args(args=args)

  if params.dir_metadata_path and not os.path.isfile(params.dir_metadata_path):
    parser.error('Dir metadata %s is missing' % params.dir_metadata_path)

  if params.diff_mapping_path and not os.path.isfile(params.diff_mapping_path):
    parser.error('Diff mapping %s is missing' % params.diff_mapping_path)

  return params


def main():
  params = _parse_args(sys.argv[1:])

  component_mapping = None
  if params.dir_metadata_path:
    with open(params.dir_metadata_path) as f:
      component_mapping = {
        d: md['monorail']['component']
        for d, md in json.load(f)['dirs'].items()
        if 'monorail' in md and 'component' in md['monorail']
      }

  diff_mapping = None
  if params.diff_mapping_path:
    with open(params.diff_mapping_path) as f:
      diff_mapping = json.load(f)

  assert (component_mapping is None) != (diff_mapping is None), (
    'Either component_mapping (for full-repo coverage) or diff_mapping '
    '(for per-cl coverage) must be specified.'
  )

  data = generate_json_coverage_metadata(
    params.coverage_dir,
    params.src_path,
    component_mapping,
    params.source_files,
    diff_mapping,
  )

  logging.info(
    'Writing fulfilled JavaScript coverage metadata to %s', params.output_dir
  )
  with open(os.path.join(params.output_dir, 'all.json.gz'), 'wb') as f:
    serialized_metadata = json.dumps(data, separators=(',', ':'))
    f.write(zlib.compress(serialized_metadata.encode()))


if __name__ == '__main__':
  logging.basicConfig(
    format='[%(asctime)s %(levelname)s] %(message)s', level=logging.INFO
  )
  sys.exit(main())
