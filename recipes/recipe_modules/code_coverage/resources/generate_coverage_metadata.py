#!/usr/bin/env python3
# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""This script generates the json data of the code coverage using llvm-cov."""

from __future__ import annotations

import argparse
import collections
import copy
import json
import logging
import os
import platform
import psutil
import stat
import subprocess
import sys
import tempfile
import time
import zlib

import aggregation_util
import repository_util

IS_WIN = platform.system() == 'Windows'
IS_MAC = platform.system() == 'Darwin'

# Override the default JSON separators with maximally compact ones.
_JSON_SEPARATORS = (',', ':')


def _posix_path(rawpath):
  return rawpath.replace(os.sep, '/')


def _extract_coverage_info(segments):
  """Returns the line and sub-line block coverage info based on the segments.

  Args:
    segments(list): refer to function `_to_file_record` below on the detail.

  Returns:
    A tuple (lines, uncovered_blocks).
    lines (dict): A mapping from line number to how many executions the line is.
    uncovered_blocks(dict(list)): A mapping from line number to a list of
        sub-line blocks where the code is not covered. A block is represented by
        two integers [start_column, end_column].
  """

  def _get_line_num(segment):
    """Returns line number."""
    return segment[0]

  def _get_col_num(segment):
    """Returns column number."""
    return segment[1]

  def _get_count(segment):
    """Returns number of times this segment is executed."""
    return segment[2]

  def _has_count(segment):
    """Returns True if this segment was instrumented and *not* skipped."""
    return segment[3]

  def _is_region_entry(segment):
    """Retruns True if segment enters a new region."""
    return segment[4]

  line_data = {}
  # Maps a line number to its uncovered sub-line blocks.
  # line# --> list([start_column, end_column])
  block_data = collections.defaultdict(list)

  # The most recent segment that starts from a previous line.
  wrap_segment = None

  current_line_num = 0
  current_line_segments = []
  next_segment_index = 0

  while current_line_num <= _get_line_num(segments[-1]):
    # Calculate the execution count for each line. Follow the logic in llvm-cov:
    # https://github.com/llvm-mirror/llvm/blob/3b35e17b21e388832d7b560a06a4f9eeaeb35330/lib/ProfileData/Coverage/CoverageMapping.cpp#L686
    current_line_num += 1
    if current_line_segments:
      wrap_segment = current_line_segments[-1]

    current_line_segments = []
    while (next_segment_index < len(segments) and
           _get_line_num(segments[next_segment_index]) == current_line_num):
      current_line_segments.append(segments[next_segment_index])
      next_segment_index += 1

    def _is_start_of_region(segment):
      return _has_count(segment) and _is_region_entry(segment)

    line_starts_new_region = any(
        _is_start_of_region(segment) for segment in current_line_segments)
    is_start_of_skipped_region = (
        current_line_segments and not _has_count(current_line_segments[0]) and
        _is_region_entry(current_line_segments[0]))
    is_coverable = not is_start_of_skipped_region and (
        (wrap_segment and _has_count(wrap_segment)) or line_starts_new_region)
    if not is_coverable:
      continue

    execution_count = 0
    if wrap_segment:
      execution_count = _get_count(wrap_segment)

    for segment in current_line_segments:
      if _is_start_of_region(segment):
        execution_count = max(execution_count, _get_count(segment))

    line_data[current_line_num] = execution_count

    # Calculate the uncovered blocks within the current line. Follow the logic
    # in llvm-cov:
    # https://github.com/llvm-mirror/llvm/blob/993ef0ca960f8ffd107c33bfbf1fd603bcf5c66c/tools/llvm-cov/SourceCoverageViewText.cpp#L114
    if execution_count == 0:
      # Skips calculating uncovered blocks if the whole line is not covered.
      continue

    col_start = 1
    is_block_not_covered = (
        wrap_segment and _has_count(wrap_segment) and
        _get_count(wrap_segment) == 0)
    for segment in current_line_segments:
      col_end = _get_col_num(segment)
      if is_block_not_covered:
        block_data[_get_line_num(segment)].append([col_start, col_end - 1])

      is_block_not_covered = (_has_count(segment) and _get_count(segment) == 0)
      col_start = col_end
    # Handle the last segment.
    if is_block_not_covered:
      last_segment = current_line_segments[-1]
      # Use -1 to indicate block extends to end of line.
      block_data[_get_line_num(last_segment)].append([col_start, -1])

  return line_data, block_data


def _to_compressed_format(line_data, block_data):
  """Turns output of `_extract_coverage_info` to a compressed format."""
  line_data = sorted(list(line_data.items()), key=lambda x: x[0])
  lines = []
  # Aggregate contiguous blocks of lines with the exact same hit count.
  last_index = 0
  for i in range(1, len(line_data) + 1):
    is_continous_line = (
        i < len(line_data) and line_data[i][0] == line_data[i - 1][0] + 1)
    has_same_count = (
        i < len(line_data) and line_data[i][1] == line_data[i - 1][1])

    # Merge two lines iff they have continous line number and exactly the same
    # count. For example: (101, 10) and (102, 10).
    if (is_continous_line and has_same_count):
      continue

    lines.append({
        'first': line_data[last_index][0],
        'last': line_data[i - 1][0],
        'count': line_data[last_index][1],
    })
    last_index = i

  uncovered_blocks = []
  for line_number in sorted(block_data.keys()):
    ranges = []
    for start, end in block_data[line_number]:
      ranges.append({
          'first': start,
          'last': end,
      })
    uncovered_blocks.append({
        'line': line_number,
        'ranges': ranges,
    })

  return lines, uncovered_blocks


def _rebase_line_and_block_data(line_data, block_data, line_mapping):
  """Rebases the line numbers of the data according to the diff mapping.

  If the file is not in the mapping, then this function is non-op.

  Args:
    line_data (dict): A mapping from line number to corresponding
                      execution count.
    block_data (dict): A mapping from line number to a list of sub-line blocks
                      where the code is not covered. A block is represented by
                      two integers [start_column, end_column].
    line_mapping(dict): A map that maps from local diff's line number to Gerrit
                      diff's line number as well as the line itself.

  Returns:
    A tuple of line_data and block with line numbers being rebased.
  """
  rebased_line_data = {}
  for line_num, count in line_data.items():

    if str(line_num) not in line_mapping:
      continue

    rebased_line_num = line_mapping[str(line_num)][0]
    rebased_line_data[rebased_line_num] = count

  rebased_block_data = {}
  for line_num, subline_blocks in block_data.items():
    if str(line_num) not in line_mapping:
      continue

    rebased_line_num = line_mapping[str(line_num)][0]
    rebased_block_data[rebased_line_num] = subline_blocks

  return rebased_line_data, rebased_block_data


def _to_compressed_file_record(file_coverage_data,
                               diff_mapping=None,
                               third_party_inclusion_subdirs=None):
  """Converts the given Clang file coverage data to coverage metadata format.

  Coverage metadata format:
  https://chromium.googlesource.com/infra/infra/+/refs/heads/main/appengine/findit/model/proto/code_coverage.proto

  Args:
    file_coverage_data (dict): The file coverage data from clang with format
      {
        "segments": [[3, 26, 1, True, True], ...],
        "summary": {
          "lines": {
            "count": 55,
          }
        },
        "filename": "/absolute/path/to/source.cc",
      }
      Each segment is another list with five values in the following order:
        /// The line where this segment begins.
        unsigned Line;
        /// The column where this segment begins.
        unsigned Col;
        /// The execution count, or zero if no count was recorded.
        uint64_t Count;
        /// When false, the segment was uninstrumented or skipped.
        bool HasCount;
        /// Whether this enters a new region or returns to a previous count.
        bool IsRegionEntry;
    diff_mapping: A map whose key is a file name that is relative to the source
                  root, and the corresponding value is another map that maps
                  from local diff's line number to Gerrit diff's line number as
                  well as the line itself.
    third_party_inclusion_subdirs (list): List of third_party subdirs to be
                  included in the aggregation

  Returns:
    A json conforming to `File` proto representing the file coverage.
  """
  coverage_path = file_coverage_data['filename']
  # exclude third_party/ code
  if ('third_party/' in coverage_path and third_party_inclusion_subdirs and
      not any(x in coverage_path for x in third_party_inclusion_subdirs)):
    return None

  line_data, block_data = _extract_coverage_info(file_coverage_data['segments'])

  if diff_mapping is not None and coverage_path in diff_mapping:
    line_mapping = diff_mapping[coverage_path]
    line_data, block_data = _rebase_line_and_block_data(line_data, block_data,
                                                        line_mapping)

  lines, uncovered_blocks = _to_compressed_format(line_data, block_data)
  data = {
      'path':  # Convert filesystem path to a source-absolute (GN-style) path.
          '//' + coverage_path,
      'lines':
          lines,
      'summaries':
          _get_clang_summary_metrics(file_coverage_data['summary']),
  }
  if uncovered_blocks:
    data['uncovered_blocks'] = uncovered_blocks

  return data


def _compute_llvm_args(profdata_path,
                       llvm_cov_path,
                       build_dir,
                       binaries,
                       sources=None,
                       num_threads=None,
                       exclusions=None,
                       summary_only=False,
                       arch=None):
  # Use as many cpu cores as possible for parallel processing of huge data.
  # Leave 5 cpu cores out for other processes in the bot.
  num_threads_arg = num_threads or max(10, psutil.cpu_count() - 5)

  # TODO(crbug.com/1068345): llvm-cov fails with a thread resource
  # unavailable exception if using more than one thread in iOS builder.
  if IS_MAC and arch == 'x86_64':
    num_threads_arg = 1

  args = [
      llvm_cov_path,
      'export',
      '-skip-expansions',
      '-skip-functions',
      '-num-threads',
      str(num_threads_arg),
      '-compilation-dir',
      build_dir,
  ]

  if exclusions:
    args.extend(['-ignore-filename-regex', exclusions])

  if summary_only:
    args.append('-summary-only')

  if arch:
    # The number of -arch=some_arch arguments needs to be the same as the number
    # of binaries passed to llvm-cov command. Nth entry in the arch list
    # corresponds to the Nth specified binary.
    args.extend(['-arch=%s' % arch] * len(binaries))

  args.extend(['-instr-profile', profdata_path, binaries[0]])
  for b in binaries[1:]:
    args.append('-object')
    args.append(b)
  args.extend(sources or [])

  return args


def _show_system_resource_usage(proc):
  if proc is None:
    return

  def bytes_to_gb(num):
    if num is None:
      return 'N/A'
    return '%.2fG' % (num / 1024.0 / 1024 / 1024)

  # Dump the memory, cpu, and disk io usage of the process.
  try:
    logging.info('Thread numbers: %d', proc.num_threads())

    p_mem = proc.memory_info()
    if IS_WIN or IS_MAC:
      logging.info('llvm-cov Memory: '
                   'RSS=%s,  VMS=%s', bytes_to_gb(p_mem.rss),
                   bytes_to_gb(p_mem.vms))
    else:
      logging.info('llvm-cov Memory: '
                   'RSS=%s,  VMS=%s, shared=%s', bytes_to_gb(p_mem.rss),
                   bytes_to_gb(p_mem.vms), bytes_to_gb(p_mem.shared))

    os_vm = psutil.virtual_memory()
    if IS_WIN or IS_MAC:
      logging.info('OS virtual Memory: '
                   'available=%s, used=%s, free=%s',
                   bytes_to_gb(os_vm.available), bytes_to_gb(os_vm.used),
                   bytes_to_gb(os_vm.free))
    else:
      logging.info(
          'OS virtual Memory: '
          'available=%s, used=%s, free=%s, cached=%s, shared=%s',
          bytes_to_gb(os_vm.available), bytes_to_gb(os_vm.used),
          bytes_to_gb(os_vm.free), bytes_to_gb(os_vm.cached),
          bytes_to_gb(os_vm.shared))

    os_sm = psutil.swap_memory()
    logging.info('OS swap: '
                 'used=%s, free=%s', bytes_to_gb(os_sm.used),
                 bytes_to_gb(os_sm.free))

    p_cpu_times = proc.cpu_times()
    cpu_percent = proc.cpu_percent(interval=1)
    logging.info(
        'llvm-cov CPU: '
        'user=%.2f hours, sys=%.2f hours, percent=%.2f%%',
        p_cpu_times.user / 60. / 60, p_cpu_times.system / 60. / 60, cpu_percent)

    os_disk_io = psutil.disk_io_counters()
    logging.info('OS-level disk io: write=%s, read=%s',
                 bytes_to_gb(os_disk_io.write_bytes),
                 bytes_to_gb(os_disk_io.read_bytes))
    if not IS_MAC:
      p_disk_io = proc.io_counters()
      logging.info('llvm-cov disk io: write=%s, read=%s',
                   bytes_to_gb(p_disk_io.write_bytes),
                   bytes_to_gb(p_disk_io.read_bytes))
  except psutil.Error:  # The process might already have finished.
    pass
  # TODO(crbug.com/1203700): Remove the except block after psutil is in a newer
  # version.
  except ValueError as error:
    logging.warning('ValueError caught when showing system info: %s', error)


def _get_raw_coverage_data(profdata_path, llvm_cov_path, build_dir, binaries,
                           sources, output_dir, exclusions, arch):
  """Creates a coverage.json object in output_dir and returns its content."""
  coverage_json_file = os.path.join(output_dir, 'coverage_raw.json')
  error_out_file = os.path.join(output_dir, 'llvm_cov.stderr.log')
  p = None
  try:

    with open(coverage_json_file, 'w') as f_out, open(error_out_file,
                                                      'w') as f_error:
      args = _compute_llvm_args(
          profdata_path,
          llvm_cov_path,
          build_dir,
          binaries,
          sources,
          exclusions=exclusions,
          arch=arch)
      logging.info('LLVM command = %s', ' '.join(args))
      if os.path.exists(profdata_path):
        logging.info('profdata file %s exists, size: %d bytes', profdata_path,
                     os.path.getsize(profdata_path))
      else:
        logging.warning('profdata file %s does not exist', profdata_path)
      logging.info('Target binaries count: %d', len(binaries))
      p = subprocess.Popen(args, stdout=f_out, stderr=f_error)
      llvm_cov_proc = None
      try:
        llvm_cov_proc = psutil.Process(p.pid)
      except psutil.Error:  # The process might already finish.
        pass

      min_duration_seconds = 5
      max_duration_seconds = 5 * 60  # 5 minutes
      duration_seconds = min_duration_seconds

      while p.poll() is None:
        _show_system_resource_usage(llvm_cov_proc)
        logging.info('-----------------waiting %d seconds...', duration_seconds)
        time.sleep(duration_seconds)
        duration_seconds = min(duration_seconds * 2, max_duration_seconds)

  finally:
    # Wait for llvm in case the above code ran into uncaught exceptions.
    if p is not None:
      returncode = p.wait()
      with open(error_out_file) as error_f:
        stderr_content = error_f.read()

      if returncode != 0:
        logging.error('Subprocess returned error %d', returncode)
        if stderr_content.strip():
          logging.error('--------dumping stderr from %s -----', error_out_file)
          logging.error('%s', stderr_content)
        sys.exit(returncode)
      elif stderr_content.strip():
        logging.info('-------- llvm-cov stderr output (exit 0) -----')
        logging.info('%s', stderr_content.strip())

  logging.info('---------------------Processing metadata--------------------')
  if p and p.returncode == 0:
    if os.path.exists(coverage_json_file):
      logging.info('coverage_raw.json size: %d bytes',
                   os.path.getsize(coverage_json_file))
    with open(coverage_json_file, 'r') as f:
      return json.load(f)


def _split_metadata_in_shards_if_necessary(output_dir, files_dir,
                                           compressed_files,
                                           directory_summaries,
                                           component_summaries):
  """Splits the metadata in a sharded manner if there are too many files.

  Args:
    output_dir: Absolute path output directory for the generated artifacts.
    files_dir: Subdirectory of output directory containing the generated
              artifacts.
    compressed_files: A list of json object that stores coverage info for files
                      in compressed format. Used by both per-cl coverage and
                      full-repo coverage.
    directory_summaries: A json object that stores coverage info for
                         directories, and the root src directory is represented
                         as '//'. Used only by full-repo coverage.
    component_summaries: A json object that stores coverage info for components.
                         Used only by full-repo coverage.
  """
  # 'dirs', 'components' and 'summaries' are only meanningful to full-repo
  # coverage.
  compressed_data = {
      'dirs':
          list(directory_summaries.values()) if directory_summaries else None,
      'components':
          list(component_summaries.values()) if component_summaries else None,
      'summaries':
          directory_summaries['//']['summaries']
          if directory_summaries else None,
  }

  # Try to split the files into 30 shards, with each shard having at least
  # 1000 files and at most 2000 files.
  # This is to have smaller data chunk to avoid Out-Of-Memory errors when the
  # data is processed on Google App Engine.
  files_in_a_shard = max(min(len(compressed_files) // 30, 2000), 1000)

  if len(compressed_files) <= files_in_a_shard:
    compressed_data['files'] = compressed_files
  else:
    # There are too many files, and they should be sharded.
    files_slice = []
    index = 0
    while True:
      start = index * files_in_a_shard
      if start >= len(compressed_files):
        break
      files_slice.append(compressed_files[start:start + files_in_a_shard])
      index += 1

    os.mkdir(os.path.join(output_dir, files_dir))
    file_shard_paths = []
    for i, files in enumerate(files_slice):
      file_name = 'files%d.json.gz' % (i + 1)
      with open(os.path.join(output_dir, files_dir, file_name), 'wb') as f:
        serialized_files = json.dumps({'files': files},
                                      separators=_JSON_SEPARATORS)
        f.write(zlib.compress(serialized_files.encode()))
      path = os.path.normpath(os.path.join(files_dir, file_name))
      file_shard_paths.append(_posix_path(path))
    compressed_data['file_shards'] = file_shard_paths

  return compressed_data


def _get_per_target_coverage_summary(profdata_path, llvm_cov_path, build_dir,
                                     binaries, arch):
  logging.info('Generating per-target coverage summaries ...')
  summaries = {}
  for binary in binaries:
    args = _compute_llvm_args(
        profdata_path,
        llvm_cov_path,
        build_dir, [binary],
        summary_only=True,
        arch=arch)
    try:
      output = subprocess.check_output(args, text=True)
      summaries[binary] = json.loads(output)['data'][0]['totals']
    except subprocess.CalledProcessError as e:
      logging.warning('Summary for binary %s failed with return code %d',
                      binary, e.returncode)
      logging.warning('%s', e.output)
      continue
    except (ValueError, TypeError):
      logging.warning('Invalid JSON output for binary %s', binary)
      logging.warning('%s', output)
      continue
  logging.info('Done generating per-target coverage summaries')
  return summaries


def _cleanup_coverage_data(src_path, llvm_raw_data):
  """Performs cleanup on raw coverage data like rebasing file paths etc.

  Returns the cleaned up coverage data in the llvm format. For more details
  on format see _to_compressed_file_record().
  """
  cleaned_file_data = []
  raw_file_count = 0
  skipped_out_count = 0
  skipped_empty_segments_count = 0
  sample_raw_filenames = []
  for datum in llvm_raw_data.get('data', []):
    for file_coverage_data in datum.get('files', []):
      raw_file_count += 1
      if len(sample_raw_filenames) < 5:
        sample_raw_filenames.append(file_coverage_data.get('filename', ''))
      prefixes = [
          src_path,
          r'C:\botcode\w',  # crbug.com/1010267
          '/b/f/w',  # crbug.com/1061603
          '/b/s/w/ir/cache/builder/src',  # crbug.com/1208128
          '/this/path/is/set'  # crbug.com/1208128
      ]
      filename = os.path.normpath(file_coverage_data['filename'])
      for prefix in prefixes:
        if filename.startswith(prefix):
          filename = filename[len(prefix):]
          break
      filename = _posix_path(filename).lstrip('/')
      # Do not generate coverage for out/ paths as it consists of automatically
      # generated code.
      if filename.startswith('out/'):
        skipped_out_count += 1
        continue
      segments = file_coverage_data['segments']
      if not segments:
        skipped_empty_segments_count += 1
        continue
      cleaned_file_data.append({
          'filename': filename,
          'segments': segments,
          'summary': file_coverage_data['summary']
      })
  if sample_raw_filenames:
    logging.info('Sample raw filenames from llvm-cov export (first %d): %s',
                 len(sample_raw_filenames), sample_raw_filenames)
  logging.info(
      'Coverage data cleanup complete: total raw files=%d, retained=%d, '
      'skipped out/=%d, skipped empty segments=%d', raw_file_count,
      len(cleaned_file_data), skipped_out_count, skipped_empty_segments_count)
  return {
      'data': [{
          'files': cleaned_file_data
      }],
      'type': llvm_raw_data['type'],
      'version': llvm_raw_data['version']
  }


def _write_coverage_to_disk(output_dir, output_file_name, data):
  """Writes coverage data to disk.

  Args:
    output_dir(string): Directory where coverage data would be materialized.
    output_file_name(string): Name of the file being materialized.
    data(dict): Coverage data of following format
      {
        'data': [{
            'files': file_data
        }],
        'type': 'llvm.coverage.json.export',
        'version': '2.0.1'
      }
    where file_data is a list of file coverage data. See
    _to_compressed_file_record() to understand how file coverage data looks
    like.
  """
  coverage_file = os.path.join(output_dir, output_file_name)
  with open(coverage_file, 'w') as fp:
    json.dump(data, fp, separators=_JSON_SEPARATORS)


def _split_llvm_data_in_shards(data, shard_size=500):
  """Splits llvm coverage data into smaller shards."""
  shards = []
  for datum in data['data']:
    files_data = datum['files']
    count = 0
    while count < len(files_data):
      files_in_shard = files_data[count:count + shard_size]
      shard = {
          'data': [{
              'files': files_in_shard
          }],
          'type': data['type'],
          'version': data['version']
      }
      shards.append(shard)
      count += shard_size
  return shards


def _get_data_from_path(data_path):
  with open(data_path) as f:
    return json.load(f)


def _generate_metadata(src_path,
                       output_dir,
                       profdata_path,
                       llvm_cov_path,
                       build_dir,
                       binaries,
                       component_mapping,
                       sources,
                       diff_mapping=None,
                       exclusions=None,
                       third_party_inclusion_subdirs=None,
                       arch=None,
                       is_fuzz_coverage=False):
  """Generates code coverage metadata.

  Args:
    src_path: Absolute path to the root checkout.
    output_dir: Output directory for the generated artifacts.
    profdata_path: Absolute path to the merged profdata file.
    llvm_cov_path: Absolute path to the llvm-cov executable.
    binaries: List of absolute path to binaries to get coverage for.
    component_mapping: A json object that stores the mapping from dirs to
                       monorail components. Only meaningful to full-repo
                       coverage.
    sources: List of absolute paths to get coverage for. Only meaningful to
             per-cl coverage.
    diff_mapping: A json object that stores the diff mapping. Only meaningful to
                  per-cl coverage.
    exclusions: A regex string to exclude matches from aggregation.
    third_party_inclusion_subdirs (list): List of third_party subdirs to be
              included in the aggregation
    arch: A string indicating the architecture of the binaries.
    is_fuzz_coverage: A bool indicating whether we are generating fuzz coverage
                      data. When True, it will (1) use the passed-in profdata
                      file rather than generating one, and (2) unset
                      `third_party_inclusion_subdirs`, `component_mapping`,
                      `diff_mapping`, `binaries`, and `arch`.

  Returns:
    A tuple (data, summaries) where:
    data: A data structure that can be serialized according to the
                    coverage metadata format.
    summaries: A dict that maps binary name to a summary of that binary's
               coverage data.
  """
  logging.info('Generating coverage metadata ...')
  logging.info(
      'Metadata config: src_path=%s, exclusions=%s, '
      'third_party_inclusion_subdirs=%s, arch=%s', src_path, exclusions,
      third_party_inclusion_subdirs, arch)
  start_time = time.time()
  raw_data = ''
  if not is_fuzz_coverage:
    raw_data = _get_raw_coverage_data(profdata_path, llvm_cov_path, build_dir,
                                      binaries, sources, output_dir, exclusions,
                                      arch)
  else:
    logging.info('The variable llvm_cov_path is %s' % llvm_cov_path)
    raw_data = _get_raw_coverage_data(profdata_path, llvm_cov_path, build_dir,
                                      binaries, sources, output_dir, exclusions,
                                      arch)
    third_party_inclusion_subdirs = None
    component_mapping = None
    diff_mapping = None
    binaries = []
    arch = None

  data = _cleanup_coverage_data(src_path, raw_data)
  _write_coverage_to_disk(output_dir, 'coverage.json', data)

  data_shards = _split_llvm_data_in_shards(data)
  for i in range(len(data_shards)):
    file_name = 'coverage%d.json' % i
    _write_coverage_to_disk(output_dir, file_name, data_shards[i])

  minutes = (time.time() - start_time) / 60
  logging.info(
      'Generating & loading coverage metadata with "llvm-cov export" '
      'took %.0f minutes', minutes)

  logging.info('Processing coverage data ...')
  start_time = time.time()
  files_coverage = []
  total_files_evaluated = 0
  third_party_skipped = 0
  for datum in data['data']:
    for file_data in datum['files']:
      total_files_evaluated += 1
      record = _to_compressed_file_record(file_data, diff_mapping,
                                          third_party_inclusion_subdirs)
      if record:
        files_coverage.append(record)
      else:
        third_party_skipped += 1

  logging.info(
      'Processed coverage files: evaluated=%d, added=%d, '
      'third_party skipped=%d', total_files_evaluated, len(files_coverage),
      third_party_skipped)
  if not files_coverage:
    logging.warning(
        'files_coverage is empty! No valid file coverage records produced.')

  per_directory_coverage = {}
  per_component_coverage = {}
  if diff_mapping is None:
    per_directory_coverage, per_component_coverage = (
        aggregation_util.get_aggregated_coverage_data_from_files(
            files_coverage, component_mapping))

  summaries = _get_per_target_coverage_summary(profdata_path, llvm_cov_path,
                                               build_dir, binaries, arch)

  if (diff_mapping is None):
    repository_util.AddGitRevisionsToCoverageFilesMetadata(
        files_coverage, src_path, 'DEPS')

  minutes = (time.time() - start_time) / 60
  logging.info('Processing coverage data took %.0f minutes', minutes)

  logging.info('Dumping aggregated data ...')
  start_time = time.time()

  compressed_data = _split_metadata_in_shards_if_necessary(
      output_dir, 'files_coverage', files_coverage, per_directory_coverage,
      per_component_coverage)
  minutes = (time.time() - start_time) / 60
  logging.info('Generating coverage metadata took %.0f minutes', minutes)

  return compressed_data, summaries


def _get_clang_summary_metrics(clang_summary):
  """Converts Clang summary format to metadata format.

  Args:
    clang_summary (dict): A dict whose keys are ('lines', 'branches', ...),
                    and corresponding values are other dicts with format:
                     {'covered': int, 'count': int}.turns:
    A list that conforms to the Metric proto at
    https://chromium.googlesource.com/infra/infra/+/refs/heads/main/appengine/findit/model/proto/code_coverage.proto
  """
  # Clang uses 'lines'/'branches'... whereas it's preferrable to use
  # singular forms in metadata format.
  singular = {
      'lines': 'line',
      'branches': 'branch',
      'regions': 'region',
      'functions': 'function',
      'instantiations': 'instantiation',
      # Modified condition/decision coverage
      # See https://llvm.org/devmtg/2022-11/slides/TechTalk4-MCDC-EnablingSafetyCriticalCodeCoverage.pdf
      'mcdc': 'mcdc'
  }
  summaries = []
  for k, v in clang_summary.items():
    if k in singular:
      summary = {
          'name': singular[k],
          'covered': v['covered'],
          'total': v['count']
      }
      summaries.append(summary)
    else:
      raise Exception("Unexpected coverage metric %s" % k)
  return summaries


def _create_index_html(output_dir):
  """Creates an index.html that lists the files within the directory.

  output_dir: The directory to create index.html for.
  """
  all_files = []
  for root, _, files in os.walk(output_dir):
    for f in files:
      all_files.append(os.path.relpath(os.path.join(root, f), output_dir))
  with open(os.path.join(output_dir, 'index.html'), 'w') as index_f:
    for f in sorted(all_files):
      f = _posix_path(f)
      index_f.write('<a href="./%s">%s<a>\n' % (f, f))
      index_f.write('<br>')


def _parse_args(args):
  parser = argparse.ArgumentParser(
      description='Generate the coverage data in metadata format')
  parser.add_argument(
      '--build-dir',
      required=True,
      type=str,
      help='absolute path to the build directory')
  parser.add_argument(
      '--src-path',
      required=True,
      type=str,
      help='absolute path to the code checkout')
  parser.add_argument(
      '--output-dir',
      required=True,
      type=str,
      help='absolute path to the directory to store the metadata, must exist')
  parser.add_argument(
      '--profdata-path',
      required=True,
      type=str,
      help='absolute path to the merged profdata')
  parser.add_argument(
      '--llvm-cov',
      required=True,
      type=str,
      help='absolute path to llvm-cov executable')
  parser.add_argument(
      '--binaries',
      nargs='+',
      type=str,
      help='absolute path to binaries to generate the coverage for')
  parser.add_argument(
      '--dir-metadata-path',
      type=str,
      help='absolute path to json file mapping dirs to metadata')
  parser.add_argument(
      '--sources',
      nargs='*',
      type=str,
      help='the source files to generate the coverage for, path should be '
      'relative to the root of the code checkout')
  parser.add_argument(
      '--diff-mapping-path',
      type=str,
      help='absolute path to the file that stores the diff mapping')
  parser.add_argument(
      '--exclusion-pattern',
      type=str,
      help='regex pattern for sources to exclude from aggregation')
  parser.add_argument(
      '--third-party-inclusion-subdirs',
      nargs='*',
      type=str,
      help='third_party sub directories to include in aggregation')
  parser.add_argument(
      '--arch',
      type=str,
      help='architecture of binaries',
  )
  parser.add_argument('--llvm-raw-data', type=str, help='llvm raw data')
  parser.add_argument(
      '--fuzz',
      action='store_true',
      help='indicates whether we are generating fuzzing coverage')
  return parser.parse_args(args=args)


def _validate_params_for_code_coverage(params):
  if not os.path.exists(params.build_dir):
    raise RuntimeError('Build directory %s must exist' % params.build_dir)
  if not os.path.exists(params.output_dir):
    raise RuntimeError('Output directory %s must exist' % params.output_dir)

  if not os.path.isfile(params.llvm_cov):
    raise RuntimeError('%s must exist' % params.llvm_cov)
  if not os.access(params.llvm_cov, os.X_OK):
    logging.info('Setting executable bit of %s', params.llvm_cov)
    os.chmod(params.llvm_cov, stat.S_IRUSR | stat.S_IXUSR | stat.S_IWUSR)
    assert os.access(params.llvm_cov, os.X_OK), 'Failed to set executable bit'

  if not os.path.isfile(params.profdata_path):
    raise RuntimeError('Input data %s is missing' % params.profdata_path)

  if (params.dir_metadata_path and
      not os.path.isfile(params.dir_metadata_path)):
    raise RuntimeError('Dir metadata %s is missing' % params.dir_metadata_path)

  if params.diff_mapping_path and not os.path.isfile(params.diff_mapping_path):
    raise RuntimeError('Diff mapping %s is missing' % params.diff_mapping_path)


def main():
  params = _parse_args(sys.argv[1:])
  _validate_params_for_code_coverage(params)

  component_mapping = None
  if params.dir_metadata_path:
    with open(params.dir_metadata_path) as f:
      component_mapping = {
          d: md['monorail']['component']
          for d, md in json.load(f)['dirs'].items()
          if 'monorail' in md and 'component' in md['monorail']
      }

  sources = params.sources or []
  abs_sources = [os.path.join(params.src_path, s) for s in sources]

  diff_mapping = None
  if params.diff_mapping_path:
    with open(params.diff_mapping_path) as f:
      diff_mapping = json.load(f)

  assert (component_mapping is None) or (diff_mapping is None), (
      'component_mapping (for full-repo coverage) and diff_mapping '
      '(for per-cl coverage) cannot be specified at the same time.')

  data, summaries = _generate_metadata(
      params.src_path,
      params.output_dir,
      params.profdata_path,
      params.llvm_cov,
      params.build_dir,
      params.binaries,
      component_mapping,
      abs_sources,
      diff_mapping,
      params.exclusion_pattern,
      params.third_party_inclusion_subdirs,
      params.arch,
      is_fuzz_coverage=params.fuzz)

  with open(os.path.join(params.output_dir, 'all.json.gz'), 'wb') as f:
    serialized_metadata = json.dumps(data, separators=_JSON_SEPARATORS)
    f.write(zlib.compress(serialized_metadata.encode()))
  with open(os.path.join(params.output_dir, 'per_target_summaries.json'),
            'w') as f:
    json.dump(summaries, f, separators=_JSON_SEPARATORS)
  _create_index_html(params.output_dir)


if __name__ == '__main__':
  logging.basicConfig(
      format='[%(asctime)s %(levelname)s] %(message)s', level=logging.INFO)
  sys.exit(main())
