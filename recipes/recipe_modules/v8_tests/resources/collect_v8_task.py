#!/usr/bin/env python
# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations
import json
import optparse
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import logging

MISSING_SHARDS_MSG = r"""Missing results from the following shard(s): %s

It can happen in following cases:
  * Test failed to start (missing *.dll/*.so dependency for example)
  * Test crashed or hung
  * Task expired because there are not enough bots available and are all used
  * Swarming service experiences problems

Please examine logs to figure out what happened.
"""

_log = logging.getLogger("collect_v8_task")
logging.basicConfig()
_log.setLevel(logging.INFO)


class BadShards:
  def __init__(self):
    self.missing = set()
    self.incomplete = set()

  def add_incomplete(self, shard):
    self.incomplete.add(shard)

  def add_missing(self, shard):
    self.missing.add(shard)

  def not_empty(self):
    return self.missing or self.incomplete

  def as_str(self):
    return ', '.join(map(str, sorted(self.missing | self.incomplete)))

  def missing_count(self):
    return len(self.missing)


class AggregatedResults:
  def __init__(self, top_tests_cutoff):
    self.max_rss_tests = []
    self.max_vms_tests = []
    self.slowest_tests = []
    self.results = []
    self.test_total = 0
    self.top_tests_cutoff = top_tests_cutoff

  def append(self, json_data):
    assert isinstance(json_data, dict)
    self.slowest_tests.extend(json_data['slowest_tests'])
    self.max_rss_tests.extend(json_data.get('max_rss_tests', []))
    self.max_vms_tests.extend(json_data.get('max_vms_tests', []))
    self.results.extend(json_data['results'])
    self.test_total += json_data['test_total']

  def as_json(self, tags):
    def sorted_tests(test_list, key):
      result = sorted(test_list, key=lambda t: t[key], reverse=True)
      return result[: self.top_tests_cutoff]

    return {
      'max_rss_tests': sorted_tests(self.max_rss_tests, 'max_rss'),
      'max_vms_tests': sorted_tests(self.max_vms_tests, 'max_vms'),
      'slowest_tests': sorted_tests(self.slowest_tests, 'duration'),
      'results': self.results,
      'tags': sorted(tags),
      'test_total': self.test_total,
    }


class TaskCollector:
  def __init__(self):
    self.warnings = []

  def emit_warning(self, title, log=''):
    """Aggregates warnings as tuples, returned as a json list in the end."""
    self.warnings.append([title, log])

  def get_shards_info(self, output_dir):
    # summary.json is produced by swarming.py itself. We are mostly interested
    # in the number of shards.
    _log.info('Reading summary.json')
    try:
      with open(os.path.join(output_dir, 'summary.json')) as f:
        summary = json.load(f)
      return summary['shards']
    except (IOError, ValueError):
      _log.error('summary.json is missing or can not be read')
      self.emit_warning(
        'summary.json is missing or can not be read',
        'Something is seriously wrong with swarming_client/ or the bot.',
      )
      return None

  def merge_shard_results(self, output_dir, shards, options):
    """Reads JSON test output from all shards and combines them into one.

    Also merges sancov coverage data if coverage_dir is spefied.

    Returns dict with merged test output on success or None on failure. Emits
    annotations.
    """
    _log.info('Merging shard results')
    if not shards:
      return None

    # Merge all JSON files together.

    tags = set()
    aggregated_results = AggregatedResults(options.top_tests_cutoff)
    bad_shards = BadShards()
    for index, result in enumerate(shards):
      _log.info('Merging shard %d' % index)
      if result is None:
        # Bot died, or anything that aborts the task ungracefully.
        bad_shards.add_missing(index)
        continue
      exit_code = result.get('exit_code')
      if exit_code is None:
        # Unclear if this can happen. Bot returnes a result, but doesn't
        # populate the exit code, which would be an internal infra failure.
        # TODO(https://crbug.com/1511557): Figure out if this needs a better fix:
        # bad_shards.add_missing(index)
        # continue
        exit_code = 0
      exit_code = int(exit_code)
      if exit_code > 1:
        # When receiving a sigterm, the test runner terminates gracefully
        # with json output, but has a return code > 1.
        _log.error('shard %d failed with exit code %d' % (index, exit_code))
        bad_shards.add_incomplete(index)
      json_data = self.load_shard_json(
        output_dir, result['task_id'], 'output.json'
      )
      if json_data:
        # The happy case. We have results, also after sigterm.
        aggregated_results.append(json_data)
      else:
        # Unclear if this can happen. When the test-runner returns, it should
        # also add the json output.
        _log.error('shard %d did not produce output.json' % index)
        bad_shards.add_incomplete(index)

    # If some shards are missing, make it known. Continue parsing anyway. Step
    # should be red anyway, since swarming.py return non-zero exit code in that
    # case.
    if bad_shards.not_empty():
      # Not all tests run, combined JSON summary can not be trusted.
      _log.error('Some shards did not complete: %s', bad_shards.as_str())
      tags.add('UNRELIABLE_RESULTS')
      as_str = bad_shards.as_str()
      self.emit_warning(
        'some shards did not complete: %s' % as_str, MISSING_SHARDS_MSG % as_str
      )

    return aggregated_results.as_json(tags)

  def merge_test_results(self, output_dir, shards, options):
    _log.info('Merging test results')
    with open(options.merged_test_output, 'wb') as f:
      merged_data = self.merge_shard_results(output_dir, shards, options)
      f.write(json.dumps(merged_data, separators=(',', ':')).encode('utf-8'))

  def merge_coverage_data(self, output_dir, shards, options):
    # Merge coverage data if specified.
    _log.info('Merging coverage data')
    if options.coverage_dir:
      for index, result in enumerate(shards):
        _log.info('Merging coverage data of shard %d' % index)
        exit_code = subprocess.call(
          [
            sys.executable,
            '-u',
            options.sancov_merger,
            '--coverage-dir',
            options.coverage_dir,
            '--swarming-output-dir',
            os.path.join(output_dir, result['task_id']),
          ]
        )
        if exit_code:
          _log.error('error when merging coverage data of shard %d' % index)
          _log.error('exit code: %d' % exit_code)
          self.emit_warning(
            'error when merging coverage data of shard %d' % index
          )

  def load_shard_json(self, output_dir, task_id, file_name):
    """Reads JSON output of a single shard."""
    # 'output.json' is set in v8/testing.py, V8SwarmingTest.
    path = os.path.join(output_dir, task_id, file_name)
    try:
      with open(path) as f:
        return json.load(f)
    except (IOError, ValueError):
      print('Missing or invalid v8 JSON file: %s' % path, file=sys.stderr)
      return None

  def swarming_cmd(self, swarming_args, options):
    # Prepare a directory to store JSON files fetched from isolate.
    task_output_dir = tempfile.mkdtemp(
      suffix='_swarming', dir=options.temp_root_dir
    )
    # Start building the command line for swarming.py.
    cmd = [
      'swarming',
    ]

    cmd.extend(swarming_args)
    cmd.extend(
      [
        '-output-dir',
        task_output_dir,
        '-task-summary-json',
        os.path.join(task_output_dir, 'summary.json'),
      ]
    )
    return cmd, task_output_dir

  def parse_args(self, args):
    # Split |args| into options for shim and options for swarming.py script.
    if '--' in args:
      index = args.index('--')
      shim_args, swarming_args = args[:index], args[index + 1 :]
    else:
      shim_args, swarming_args = args, []

    # Parse shim's own options.
    parser = optparse.OptionParser()
    parser.add_option('--temp-root-dir', default=tempfile.gettempdir())
    parser.add_option('--merged-test-output')
    parser.add_option('--warnings-json')
    parser.add_option('--top-tests-cutoff', type="int", default=100)
    parser.add_option('--coverage-dir')
    parser.add_option('--sancov-merger')
    options, extra_args = parser.parse_args(shim_args)

    # Validate options.
    if extra_args:
      parser.error('Unexpected command line arguments')
    if options.coverage_dir and not options.sancov_merger:
      parser.error('--sancov-merger is required for merging coverage data')

    return options, swarming_args

  def run(self, args):
    _log.info('Running task collector')
    options, swarming_args = self.parse_args(args)
    cmd, output_dir = self.swarming_cmd(swarming_args, options)

    exit_code = 1
    try:
      # Run the real script, regardless of an exit code try to find and parse
      # JSON output files, since exit code may indicate that the isolated task
      # failed, not the swarming.py invocation itself.
      exit_code = subprocess.call(cmd)

      # Output parsing should not change exit code no matter what, so catch any
      # exceptions and just log them.
      try:
        shards = self.get_shards_info(output_dir)
        self.merge_test_results(output_dir, shards, options)
        self.merge_coverage_data(output_dir, shards, options)
      except Exception:
        _log.error('Failed to process v8 output JSON', exc_info=True)
        self.emit_warning(
          'failed to process v8 output JSON', traceback.format_exc()
        )

    finally:
      _log.info('Cleaning up output directory %s', output_dir)
      shutil.rmtree(output_dir, ignore_errors=True)

    # Aggregated warnings are passed to the collecting recipe.
    _log.info('Writing warnings to %s', options.warnings_json)
    with open(options.warnings_json, 'w') as f:
      json.dump(self.warnings, f)

    return exit_code


if __name__ == '__main__':
  sys.exit(TaskCollector().run(sys.argv[1:]))
