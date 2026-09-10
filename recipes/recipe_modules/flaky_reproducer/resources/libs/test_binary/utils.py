# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import logging
import subprocess
import os


def strip_command_wrappers(command, strip_wrappers):
  """Strip command wrapper (e.g. ResultDB integration - rdb) from command line.

  Expecting the following wrappers use -- to signify the start of actual
  commands.

  Args:
    strip_wrappers (list[str]): Name of wrapper commands.

  Returns:
    A copy of command without command wrappers.
  """
  cmd = command[:]
  stripped = True
  while cmd and stripped:
    stripped = False
    cmd_0 = cmd[0].lower()
    if cmd_0.endswith('.exe'):
      cmd_0 = cmd_0[:-4]
    if cmd_0 in strip_wrappers:
      try:
        dash_index = cmd.index('--')
      except ValueError as e:
        raise ValueError(
          'Unsupported command wrapper: {0} in {1}'.format(cmd_0, command)
        ) from e
      cmd = cmd[dash_index + 1 :]
      stripped = True
  if not cmd:
    raise ValueError('Empty command after strip: {0}'.format(command))
  return cmd


def strip_command_switches(command, strip_switches):
  """Strip specified switches and its value.

  Args:
    strip_switches (map[str, int]): Map of switch name and number of values it's
      expecting.

  Returns:
    A copy of command without strip_switches and their values.
  """
  cmd = []
  # strip switch with value like: --switch value
  strip_next_arg = 0
  for arg in command:
    if arg.startswith('-'):
      if '=' in arg:
        switch, value = arg.split('=', 1)
      else:
        switch, value = arg, None
      switch = switch.strip('- ').lower()

      if switch in strip_switches:
        strip_next_arg = 0 if value else strip_switches[switch]
      else:
        strip_next_arg = 0  # Reset when accepting a new switch.
        cmd.append(arg)
    elif strip_next_arg:
      strip_next_arg -= 1
    else:
      cmd.append(arg)
  return cmd


def strip_env_vars(env_vars, strip_keys):
  """Strip specified keys from environnement variables

  Args:
    strip_keys (list[str]): Name of the environnement variables.

  Returns:
    A copy of env_vars without strip_keys.
  """
  return {
    key: value for key, value in env_vars.items() if key not in strip_keys
  }


def run_cmd(argv, cwd=None, env=None):
  """Run command locally.

  Args:
    argv (list[str]): Sequence of program arguments.
    cwd (str): Change working directory.
    env (dict): Dict of environment variables.
  """
  # FIXME(kuanhuang): This method could be improved to handle the swarming
  # signals. Inheriting and merging the current process' environment.
  # Replace ISOLATED_OUTDIR in commandline.
  if 'ISOLATED_OUTDIR' in os.environ:
    isolated_outdir = os.environ['ISOLATED_OUTDIR']
    argv = [v.replace(r'${ISOLATED_OUTDIR}', isolated_outdir) for v in argv]
  logging.info('Running %r in %r with %r', argv, cwd, env)
  # NOTE: Windows wouldn't search for the executable in cwd set via Popen.
  # Using `chdir` before Popen to workaround this issue.
  old_cwd = os.getcwd()
  os.chdir(cwd)
  try:
    with subprocess.Popen(argv, env=env) as process:
      return process.wait()
  finally:
    os.chdir(old_cwd)
