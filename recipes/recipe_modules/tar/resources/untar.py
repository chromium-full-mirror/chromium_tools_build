# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Standalone Python script to untar an archive. Intended to be used by 'tar'
recipe module internally. Should not be used elsewhere.
"""

from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tarfile


def untar_with_subprocess(tar_file, output, quiet):
  """Untars an archive using 'tar' utility.

  Works only on Linux and Mac, uses system 'tar' program.

  Args:
    tar_file: absolute path to an archive to untar.
    output: existing directory to untar to.
    quiet (bool): If True, instruct the subprocess to untar with
        minimal output.

  Returns:
    Exit code (0 on success).
  """
  args = ['tar']
  options = '-x'
  if not quiet:
    options += 'v'
  options += 'f'
  args += [options, tar_file]
  return subprocess.call(
      args=args,
      cwd=output)


def untar_with_python(tar_file, output):
  """Untars an archive using 'tarfile' python module.

  Works everywhere where Python works (Windows and POSIX).

  Args:
    tar_file: absolute path to an archive to untar.
    output: existing directory to untar to.

  Returns:
    Exit code (0 on success).
  """
  with tarfile.open(tar_file, 'r') as tf:
    for name in tf.getnames():
      print('Extracting %s' % name)
      tf.extract(name, output)
  return 0


def main():
  # See tar/api.py, def untar(...) for format of |data|.
  data = json.load(sys.stdin)
  output = data['output']
  tar_file = data['tar_file']
  quiet = data['quiet']

  # Archive path should exist and be an absolute path to a file.
  assert os.path.exists(tar_file), tar_file
  assert os.path.isfile(tar_file), tar_file

  # Output path should be an absolute path, and should NOT exist.
  assert os.path.isabs(output), output
  assert not os.path.exists(output), output

  print('Untaring %s...' % tar_file)
  exit_code = -1
  try:
    os.makedirs(output)
    if sys.platform == 'win32':
      # Used on Windows, since there's no builtin 'untar' utility there.
      exit_code = untar_with_python(tar_file, output)
    else:
      # On mac and linux 'untar' utility handles symlink and file modes.
      exit_code = untar_with_subprocess(tar_file, output, quiet)
  finally:
    # On non-zero exit code or on unexpected exception, clean up.
    if exit_code:
      shutil.rmtree(output, ignore_errors=True)
  return exit_code


if __name__ == '__main__':
  sys.exit(main())
