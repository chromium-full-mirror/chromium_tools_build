#!/usr/bin/env python3
# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
import time


def run_command_with_output(argv, stdoutfile, env=None, cwd=None):
  """ Run command and stream its stdout/stderr to the console & |stdoutfile|.
  """
  print('Running %r in %r (env: %r)' % (argv, cwd, env))
  assert stdoutfile
  with io.open(stdoutfile, 'w') as writer, io.open(stdoutfile, 'r', 1) as \
      reader:
    process = subprocess.Popen(argv, env=env, cwd=cwd, stdout=writer,
        stderr=subprocess.STDOUT)
    while process.poll() is None:
      sys.stdout.write(reader.read())
      time.sleep(0.1)
    # Read the remaining.
    sys.stdout.write(reader.read())
    print('Command %r returned exit code %d' % (argv, process.returncode))
    return process.returncode


def collect_task(collect_cmd, merge_script, merge_script_stdout_file,
                 build_properties, merge_arguments, task_output_dir,
                 output_json, summary_json_file):
  """Collect and merge the results of a task.

  This is a relatively thin wrapper script around a `swarming.py collect`
  command and a subsequent results merge to ensure that the recipe system
  treats them as a single step. The results merge can either be the default
  one provided by results_merger or a python script provided as merge_script.

  Args:
    collect_cmd: The `swarming.py collect` command to run. Should not contain
      a --task-output-dir argument.
    merge_script: A merge/postprocessing script that should be run to
      merge the results. This script will be invoked as

        <merge_script> \
            [--build-properties <string JSON>] \
            [merge arguments...] \
            --summary-json <summary json> \
            --task-output-dir <task output directory> \
            -o <merged json path> \
            <shard json path>...

      where the merge arguments are the contents of merge_arguments_json.
    build_properties: A string containing build information to
      pass to the merge script in JSON form.
    merge_arguments: A string containing additional arguments to pass to
      the merge script in JSON form.
    merge_script_stdout_file: A path to a file in which the stdout/stderr
      of the merge script will be written to.
    task_output_dir: A path to a directory in which swarming will write the
      output of the task, including a summary JSON and all of the individual
      shard results.
    output_json: A path to a JSON file to which the merged results should be
      written. The merged results should be in the JSON Results File Format
      (https://www.chromium.org/developers/the-json-test-results-format)
      and may optionally contain a top level "links" field that may contain a
      dict mapping link text to URLs, for a set of links that will be included
      in the buildbot output.
    summary_json_file: This is to specify summary json file. Used for go client
      that does not generate summary.json under task_output_dir by default.
  Returns:
    The exit code of collect_cmd or merge_cmd.
  """
  logging.debug('Using task_output_dir: %r', task_output_dir)
  if os.path.exists(task_output_dir):
    logging.warning('task_output_dir %r already exists!', task_output_dir)
    existing_contents = []
    try:
      for p in os.listdir(task_output_dir):
        existing_contents.append(os.path.join(task_output_dir, p))
    except (OSError, IOError) as e:
      logging.error('Error while examining existing task_output_dir: %s', e)

    logging.warning('task_output_dir existing content: %r', existing_contents)

  collect_cmd.extend([
    '-output-dir', task_output_dir,
    '-task-summary-json', summary_json_file
  ])

  logging.info('collect_cmd: %s', ' '.join(collect_cmd))
  collect_result = subprocess.call(collect_cmd)
  if collect_result != 0:
    logging.warning('collect_cmd had non-zero return code: %s', collect_result)

  task_output_dir_contents = []
  try:
    task_output_dir_contents.extend(
        os.path.join(task_output_dir, p)
        for p in os.listdir(task_output_dir))
  except (OSError, IOError) as e:
    logging.error('Error while processing task_output_dir: %s', e)

  logging.debug('Contents of task_output_dir: %r', task_output_dir_contents)
  if not task_output_dir_contents:
    logging.warning('No files found in task_output_dir: %r', task_output_dir)

  task_output_subdirs = (
      p for p in task_output_dir_contents
      if os.path.isdir(p))
  shard_json_files = [
      os.path.join(subdir, 'output.json')
      for subdir in task_output_subdirs]
  extant_shard_json_files = [
      f for f in shard_json_files if os.path.exists(f)]

  logging.debug('Expecting shard_json_files: %r', shard_json_files)

  if shard_json_files != extant_shard_json_files:
    logging.warning(
        'Expected output.json file missing: %r\nFound: %r\nExpected: %r\n',
        set(shard_json_files) - set(extant_shard_json_files),
        extant_shard_json_files, shard_json_files)

  empty_shard_json_files = set(
      f for f in extant_shard_json_files if os.path.getsize(f) == 0)

  if empty_shard_json_files:
    logging.warning('One or more empty output.json files exist: %r',
                    empty_shard_json_files)
    extant_shard_json_files = [
        f for f in extant_shard_json_files if f not in empty_shard_json_files]

  if not extant_shard_json_files:
    logging.warning(
        'No shard json files found in task_output_dir: %r\nFound %r',
        task_output_dir, task_output_dir_contents)

  merge_result = 0

  # The merge script may have its own vpython spec, so we invoke it using
  # 'vpython' over sys.executable in order to prevent re-using this script's
  # vpython spec.
  merge_cmd = ['vpython3', merge_script]
  if build_properties:
    merge_cmd.extend(('--build-properties', build_properties))
  if os.path.exists(summary_json_file):
    merge_cmd.extend(('--summary-json', summary_json_file))
  else:
    logging.warning('Summary json file missing: %r', summary_json_file)
  merge_cmd.extend(('--task-output-dir', task_output_dir))
  if merge_arguments:
    merge_cmd.extend(json.loads(merge_arguments))
  merge_cmd.extend(('-o', output_json))
  merge_cmd.extend(extant_shard_json_files)

  logging.info('merge_cmd: %s', ' '.join(merge_cmd))

  merge_result = run_command_with_output(
      argv=merge_cmd, stdoutfile=merge_script_stdout_file)
  if merge_result != 0:
    logging.warning('merge_cmd had non-zero return code: %s', merge_result)

  if not os.path.exists(output_json):
    logging.warning('merge_cmd did not create output_json file: %r',
                    output_json)

  return collect_result or merge_result


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--build-properties')
  parser.add_argument('--merge-additional-args')
  parser.add_argument('--merge-script', required=True)
  parser.add_argument('--merge-script-stdout-file', required=True)
  parser.add_argument('--task-output-dir', required=True)
  parser.add_argument('-o', '--output-json', required=True)
  parser.add_argument('--verbose', action='store_true')
  parser.add_argument('--summary-json-file')

  parser.add_argument('collect_cmd', nargs='+')

  args = parser.parse_args()
  log_level = logging.DEBUG if args.verbose else logging.WARNING
  fmt = '%(asctime)s - %(name)s: [%(levelname)s] %(message)s'
  logging.basicConfig(stream=sys.stdout, level=log_level, format=fmt)

  return collect_task(args.collect_cmd, args.merge_script,
                      args.merge_script_stdout_file, args.build_properties,
                      args.merge_additional_args, args.task_output_dir,
                      args.output_json, args.summary_json_file)


if __name__ == '__main__':
  sys.exit(main())
