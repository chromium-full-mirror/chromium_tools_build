#!/usr/bin/env python3
# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Script for migrating per-builder configs from recipe to src.

When run, the script will output the snippets of starlark that need to
be added to the LUCI builder definitions in order to move the builder
configs src-side.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import subprocess
import sys


def parse_args(args=None):
  """Parse the command-line arguments.

  Args:
    args: (list[str]) Arguments to use instead of sys.argv. Used for
      unit testing.

  Returns:
    An argparse.Namespace object with the following attributes:
      * builders: (list[str]) The builders to generate the starlark for
        migrating.
  """
  parser = argparse.ArgumentParser(
    description=(
      'Produce the starlark to migrate the config '
      'for a group of related builders src-side'
    )
  )

  parser.add_argument(
    '--infra-config-dir',
    help=(
      'Path to the infra/config directory to update.'
      ' If not set, code snippets will be output to stdout instead.'
    ),
  )
  parser.add_argument(
    '--buildozer-binary',
    help=(
      'The buildozer binary to use.'
      ' The path can be an absolute or relative path,'
      ' or a simply the name of the command if it is on PATH.'
    ),
  )
  parser.add_argument(
    'builders',
    nargs='+',
    help='The builders to migrate, in the form <builder group>:<builder name>',
  )

  return parser.parse_args(args)


class InvalidBuilderError(ValueError):
  def __init__(self, invalid_builders):
    super().__init__(invalid_builders)
    self._invalid_builders = tuple(invalid_builders)

  @property
  def invalid_builders(self):
    return self._invalid_builders

  def __str__(self):
    invalid_builder_str = ', '.join(
      repr(b) for b in sorted(self.invalid_builders)
    )
    return (
      f'The following builders to migrate are invalid: {invalid_builder_str},'
      ' they must be in the form <builder group>:<builder name>'
    )


class MigrationError(Exception):
  def __init__(self, reason):
    super().__init__(reason)
    self.reason = reason

  def __str__(self):
    return self.reason


def get_builders_to_migrate(args):
  builder_groups_and_names = []
  invalid_builders = []
  for b in args.builders:
    pieces = tuple(b.split(':'))
    if len(pieces) != 2:
      invalid_builders.append(b)
    else:
      builder_groups_and_names.append(pieces)
  if invalid_builders:
    raise InvalidBuilderError(invalid_builders)
  return builder_groups_and_names


_RECIPES_PY = os.path.normpath(f'{__file__}/../../../../../recipes.py')
_BUILDOZER_WRAPPER_PY = os.path.normpath(f'{__file__}/../buildozer_wrapper.py')


def _run_builder_config_migration_recipe(
  builders_to_migrate, output_path, json_output
):
  properties = {
    'migration_operation': {
      'builders_to_migrate': [
        {'builder_group': group, 'builder': name}
        for (group, name) in builders_to_migrate
      ],
      'output_path': output_path,
      'json_output': json_output,
    },
  }

  with tempfile.TemporaryDirectory() as d:
    result_json_path = os.path.join(d, 'result.json')

    cmd = [
      sys.executable,
      _RECIPES_PY,
      'run',
      '--output-result-json',
      result_json_path,
      '--properties',
      json.dumps(properties),
      'chromium/builder_config_migration',
    ]

    env = os.environ.copy()
    env['RECIPES_USE_PY3'] = 'true'

    try:
      subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, env=env)
    except subprocess.CalledProcessError as e:
      if not os.path.exists(result_json_path):
        sys.stderr.write(e.output)
        raise
      with open(result_json_path) as f:
        result = json.load(f)
      raise MigrationError(result["failure"]["humanReason"]) from e


def _run_buildozer(input_json_path, infra_config_dir, buildozer_binary):
  cmd = [
    sys.executable,
    _BUILDOZER_WRAPPER_PY,
    '--infra-config-dir',
    infra_config_dir,
    input_json_path,
  ]
  if buildozer_binary is not None:
    cmd.extend(['--buildozer-binary', buildozer_binary])
  try:
    subprocess.run(cmd, check=True, text=True)
  except subprocess.CalledProcessError as e:
    raise MigrationError("could not update starlark files") from e


def main():
  args = parse_args()
  try:
    builders_to_migrate = get_builders_to_migrate(args)

    with tempfile.TemporaryDirectory() as d:
      if args.infra_config_dir:
        json_output = True
        output_path = os.path.join(d, 'migration.json')
      else:
        json_output = False
        output_path = os.path.join(d, 'migration.txt')

      _run_builder_config_migration_recipe(
        builders_to_migrate, output_path, json_output
      )

      if args.infra_config_dir:
        _run_buildozer(
          output_path, args.infra_config_dir, args.buildozer_binary
        )
      else:
        with open(output_path) as f:
          migration = f.read()
        print(migration)

  except (InvalidBuilderError, MigrationError) as e:
    print(str(e), file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
  main()
