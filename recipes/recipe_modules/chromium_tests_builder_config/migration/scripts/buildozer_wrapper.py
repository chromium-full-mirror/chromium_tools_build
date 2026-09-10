#!/usr/bin/env python3
# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from typing import Collection, Optional, Sequence

_EPILOG = '''\
The format of the input json file is a list of dicts with the following keys
(all required):

* builder_group - A string with the builder group of the builder.
* builder - A string with the name of the builder.
* edits - A dict where the keys are the names of the arguments to set in the
  builder definition and the corresponding values are the value for that
  argument. There should be no unescaped whitespace in the value except for
  space characters within string values.

buildozer must be installed on the system.
buildozer can be installed with the following command:

go install github.com/bazelbuild/buildtools/buildozer@latest
'''


def parse_args(args: Optional[Sequence[str]] = None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description='A wrapper around buildozer for chromium starlark.',
    epilog=_EPILOG,
  )
  parser.add_argument(
    '--infra-config-dir',
    default='.',
    help=(
      'The path to the //infra/config directory'
      ' containing starlark files to modify.'
    ),
  )
  parser.add_argument(
    '--buildozer-binary',
    default='buildozer',
    help=(
      'The buildozer binary to use.'
      ' The path can be an absolute or relative path,'
      ' or a simply the name of the command if it is on PATH.'
    ),
  )
  parser.add_argument(
    'input_json',
    help='The path to the json file containing the edits to apply.',
  )
  return parser.parse_args(args)


def _execute_buildozer(
  *,
  buildozer_binary: str,
  input_file_path: str,
  output_file_path: str,
  commands: Collection[str],
) -> None:
  try:
    os.makedirs(os.path.dirname(output_file_path))
  except FileExistsError:
    pass

  commands_file_contents = '\n'.join(commands)
  with tempfile.TemporaryDirectory() as d:
    commands_file_path = os.path.normpath(f'{d}/commands')
    with open(commands_file_path, 'w') as f:
      f.write(commands_file_contents)

    with open(input_file_path) as stdin, open(output_file_path, 'w') as stdout:
      # buildozer has non-standard exit code, we have check logic afterwards
      # pylint: disable=subprocess-run-check
      result = subprocess.run(
        [buildozer_binary, '-f', commands_file_path],
        text=True,
        stdin=stdin,
        stdout=stdout,
      )

  # buildozer won't have an exit code of 0 because that indicates that it
  # succeeded and modified files. When operating on stdin, it won't modify
  # files. So we look for exit code 3 which indicates that it succeeded
  # without modifying files.
  if result.returncode != 3:
    print(
      (
        'Failed to execute buildozer with commands file:\n'
        f'{commands_file_contents}'
      ),
      file=sys.stderr,
    )
    result.check_returncode()


def _escape_spaces(s: str) -> str:
  # Space characters may exist within string values, they need to be escaped so
  # that buildozer doesn't interpret them as the end of an argument. Other
  # whitespace characters would not be valid within a json-encoded string. All
  # other whitespace would be non-significant.
  return s.replace(' ', '\\ ')


def perform_edits(
  *,
  buildozer_binary: str,
  infra_config_dir: str,
  output_dir: str,
  builder_group: str,
  edits_by_builder: str,
) -> None:
  # TODO(gbeaty) Actually figure out what file a builder is in so that this will
  # work for builders that don't match the heuristic
  if builder_group.startswith('tryserver'):
    bucket = 'try'
  else:
    bucket = 'ci'

  commands = []
  for builder, edits in edits_by_builder.items():
    # buildozer is geared towards bazel build targets, which are of the form
    # package:target_name. Since we are operating on starlark that is not part
    # of a bazel workspace, we send the file contents on stdin; the - for the
    # package tells it to treat stdin as the starlark file to modify. Target
    # name matching simply looks for a function invocation with the name keyword
    # argument set to target_name.
    target = f'-:{builder}'
    ops = [
      f'set {attr} {_escape_spaces(value)}' for attr, value in edits.items()
    ]
    commands.append('|'.join(ops + [target]))

  path_to_edit = f'subprojects/chromium/{bucket}/{builder_group}.star'
  input_file_path = f'{infra_config_dir}/{path_to_edit}'
  output_file_path = f'{output_dir}/{path_to_edit}'

  _execute_buildozer(
    buildozer_binary=buildozer_binary,
    input_file_path=input_file_path,
    output_file_path=output_file_path,
    commands=commands,
  )


def main() -> int:
  args = parse_args()

  if shutil.which(args.buildozer_binary) is None:
    message = (
      f'buildozer binary {args.buildozer_binary}'
      ' could not be found (or is not executable).'
    )
    print(message, file=sys.stderr)
    return 1

  with open(args.input_json) as f:
    edits = json.load(f)

  edits_by_builder_by_builder_group = collections.defaultdict(dict)
  for e in edits:
    edits_by_builder = edits_by_builder_by_builder_group[e['builder_group']]
    edits_by_builder[e['builder']] = e['edits']

  try:
    with tempfile.TemporaryDirectory() as output_dir:
      for group, edits_by_builder in edits_by_builder_by_builder_group.items():
        perform_edits(
          buildozer_binary=args.buildozer_binary,
          infra_config_dir=args.infra_config_dir,
          output_dir=output_dir,
          builder_group=group,
          edits_by_builder=edits_by_builder,
        )

      shutil.copytree(output_dir, args.infra_config_dir, dirs_exist_ok=True)

    # Buildozer rearranges call arguments, so execute lucicfg fmt to restore
    # them to the configured order
    subprocess.check_call(['lucicfg', 'fmt', args.infra_config_dir])

  except subprocess.CalledProcessError as e:
    print(str(e), file=sys.stderr)
    return 1


if __name__ == '__main__':
  sys.exit(main())
