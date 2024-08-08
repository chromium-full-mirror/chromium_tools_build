# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Standalone Python script to tar a set of files. Intended to be used by 'tar'
recipe module internally. Should not be used elsewhere.
"""

from __future__ import absolute_import
from __future__ import print_function
import json
import os
import subprocess
import sys
import tarfile


def tar_with_subprocess(root, output, entries, compression):
  """tars set of files and directories using 'tar' utility.

  Works only on Linux and Mac, uses system 'tar' program.

  Args:
    root: absolute path to a directory that will become a root of the archive.
    output: absolute path to a destination archive.
    entries: list of dicts, describing what to tar, see tar/api.py.

  Returns:
    Exit code (0 on success).
  """
  # Collect paths relative to |root| of all items we'd like to tar.
  items_to_tar = []

  for entry in entries:
    tp = entry['type']
    path = entry['path']
    if tp == 'file':
      # File must exist and be inside |root|.
      assert os.path.isfile(path), path
      assert path.startswith(root), path
      items_to_tar.append(path[len(root):])
    elif entry['type'] == 'dir':
      # Append trailing '/'.
      path = path.rstrip(os.path.sep) + os.path.sep
      # Directory must exist and be inside |root| or be |root| itself.
      assert os.path.isdir(path), path
      assert path.startswith(root), path
      items_to_tar.append(path[len(root):] or '.')
    else:
      raise AssertionError('Invalid entry type: %s' % (tp,))

  # Invoke 'tar' in |root| directory.
  args = ['tar']
  options = '-cv'
  if compression in ['gz', 'bz2']:
    options += {'gz': 'z', 'bz2': 'j'}[compression]

  # Sequence of options must have f at the end to define the output.
  # -cv[z/j]f
  args += [options + 'f', output]
  args += items_to_tar
  print(('Executing command: {}'.format(args)))
  proc = subprocess.Popen(
      args=args,
      cwd=root)
  proc.communicate()
  if proc.stderr:
    print(('Error: {}'.format(proc.stderr)))
  print(('Ret code: {}'.format(proc.returncode)))
  return proc.returncode


def tar_with_python(root, output, entries, compression):
  """tars set of files and directories using 'tarfile' python module.

  Works everywhere where python works (Windows and POSIX).

  Args:
    root: absolute path to a directory that will become a root of the archive.
    output: absolute path to a destination archive.
    entries: list of dicts, describing what to tar, see tar/api.py.

  Returns:
    Exit code (0 on success).
  """
  mode = 'w'
  if compression in ['gz', 'bz2']:
    mode += ':%s' % compression
  with tarfile.open(output, mode) as tf:
    def add(path, archive_name):
      assert path.startswith(root), path
      # Do not add itself to archive.
      if path == output:
        return
      if archive_name is None:
        archive_name = path[len(root):]
      print('Adding %s' % archive_name)
      tf.add(path, archive_name)

    for entry in entries:
      tp = entry['type']
      path = entry['path']
      if tp == 'file':
        add(path, entry.get('archive_name'))
      elif tp == 'dir':
        for cur, _, files in os.walk(path):
          for name in files:
            add(os.path.join(cur, name), None)
      else:
        raise AssertionError('Invalid entry type: %s' % (tp,))
  return 0


def use_python_tar(entries):
  if sys.platform == 'win32':
    return True
  for entry in entries:
    if entry.get('archive_name') is not None:
      return True
  return False


def main():
  # See tar/api.py, def tar(...) for format of |data|.
  data = json.load(sys.stdin)
  entries = data['entries']
  output = data['output']
  compression = data['compression']
  root = data['root'].rstrip(os.path.sep) + os.path.sep

  # Archive root directory should exist and be an absolute path.
  assert os.path.exists(root), root
  assert os.path.isabs(root), root

  # Output tar path should be an absolute path.
  assert os.path.isabs(output), output

  print('Taring %s...' % output)
  exit_code = -1
  try:
    if use_python_tar(entries):
      # Used on Windows, since there's no builtin 'tar' utility there, and when
      # an explicit archive_name is set, since there's no way to do that with
      # the native tar utility without filesystem shenanigans
      exit_code = tar_with_python(root, output, entries, compression)
    else:
      # On mac and linux 'tar' utility handles symlink and file modes.
      exit_code = tar_with_subprocess(root, output, entries, compression)
  finally:
    # On non-zero exit code or on unexpected exception, clean up.
    if exit_code:
      try:
        os.remove(output)
      except:  # pylint: disable=bare-except
        pass
  if not exit_code:
    print('Archive size: %.1f KB' % (os.stat(output).st_size / 1024.0,))
  return exit_code


if __name__ == '__main__':
  sys.exit(main())
