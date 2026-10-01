#!/usr/bin/env vpython3
# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Unit tests for classes in chromium_utils.py."""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT_DIR = os.path.normpath(os.path.join(__file__, '..', '..', '..', '..'))
sys.path.extend(
  [
    os.path.join(ROOT_DIR, 'scripts'),
  ]
)

from common import chromium_utils


class FakeParser:
  def __init__(self):
    self.lines = []

  def ProcessLine(self, line):
    self.lines.append(line)


class FakeFilterObj:
  def __init__(self):
    self.lines = []

  def FilterLine(self, line):
    self.lines.append(line)

  # this is called when there is data without a trailing newline
  def FilterDone(self, line):
    self.lines.append(line)


def synthesizeCmd(args):
  basecmd = [sys.executable, '-c']
  basecmd.extend(args)
  return basecmd


class TestRunCommand(unittest.TestCase):
  def testRunCommandPlain(self):
    mycmd = synthesizeCmd(['exit()'])
    self.assertEqual(0, chromium_utils.RunCommand(mycmd, print_cmd=False))

  def testRunCommandParser(self):
    mycmd = synthesizeCmd(['print("1\\n2")'])
    parser = FakeParser()
    retval = chromium_utils.RunCommand(
      mycmd, print_cmd=False, parser_func=parser.ProcessLine
    )
    self.assertEqual(0, retval)
    self.assertEqual(['1', '2', ''], parser.lines)

  def testRunCommandFilter(self):
    mycmd = synthesizeCmd(['print("1\\n2")'])
    filter_obj = FakeFilterObj()
    retval = chromium_utils.RunCommand(
      mycmd, print_cmd=False, filter_obj=filter_obj
    )
    self.assertEqual(0, retval)
    self.assertEqual(['1\n', '2\n'], filter_obj.lines)

  def testRunCommandFilterEndline(self):
    mycmd = synthesizeCmd(['import sys; sys.stdout.write("test")'])
    filter_obj = FakeFilterObj()
    retval = chromium_utils.RunCommand(
      mycmd, print_cmd=False, filter_obj=filter_obj
    )
    self.assertEqual(0, retval)
    self.assertEqual(['test'], filter_obj.lines)

  def testRunCommandPipesParser(self):
    firstcmd = synthesizeCmd(['print("1\\n2")'])

    oneliner = "import sys; [sys.stdout.write(l.strip()+'1\\n') for l in "
    oneliner += "sys.stdin.readlines()]"
    secondcmd = synthesizeCmd([oneliner])
    parser = FakeParser()
    retval = chromium_utils.RunCommand(
      firstcmd,
      print_cmd=False,
      pipes=[secondcmd],
      parser_func=parser.ProcessLine,
    )
    self.assertEqual(0, retval)
    self.assertEqual(['11', '21', ''], parser.lines)

  def testRunCommandPipesFilter(self):
    firstcmd = synthesizeCmd(['print("1\\n2")'])

    oneliner = "import sys; [sys.stdout.write(l.strip()+'1\\n') for l in "
    oneliner += "sys.stdin.readlines()]"
    secondcmd = synthesizeCmd([oneliner])
    filter_obj = FakeFilterObj()
    retval = chromium_utils.RunCommand(
      firstcmd, print_cmd=False, pipes=[secondcmd], filter_obj=filter_obj
    )
    self.assertEqual(0, retval)
    self.assertEqual(['11\n', '21\n'], filter_obj.lines)

  def testRunCommandPipesFailure(self):
    firstcmd = synthesizeCmd(['print("1")'])

    secondcmd = synthesizeCmd(["exit(1)"])
    filter_obj = FakeFilterObj()
    retval = chromium_utils.RunCommand(
      firstcmd, print_cmd=False, pipes=[secondcmd], filter_obj=filter_obj
    )
    self.assertEqual(1, retval)


def _FakeWinLinkFunc(src, dst):
  """Mimics chromium_utils._WIN_LINK_FUNC on POSIX:
  symlink dirs, hardlink files."""
  if os.path.isdir(src):
    os.symlink(os.path.abspath(src), dst, target_is_directory=True)
  else:
    os.link(src, dst)


class TestMakeZip(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.mkdtemp()
    self.src = os.path.join(self.tmp, 'src')
    self.out = os.path.join(self.tmp, 'out')
    for rel in ('a/b/c/f1', 'a/b/f2', 'a/g'):
      path = os.path.join(self.src, *rel.split('/'))
      os.makedirs(os.path.dirname(path), exist_ok=True)
      with open(path, 'w') as f:
        f.write(rel)
    os.makedirs(self.out)

  def tearDown(self):
    shutil.rmtree(self.tmp)

  @staticmethod
  def _ListFiles(root):
    """Returns the set of files reachable under root, following links."""
    result = set()
    for dirpath, _, files in os.walk(root, followlinks=True):
      for f in files:
        rel = os.path.relpath(os.path.join(dirpath, f), root)
        result.add(rel.replace(os.sep, '/'))
    return result

  def _MakeZip(self, file_list, **kwargs):
    src_before = self._ListFiles(self.src)
    archive_dir, zip_file = chromium_utils.MakeZip(
      self.out, 'archive', file_list, self.src, **kwargs
    )
    self.assertTrue(os.path.exists(zip_file))
    # Staging must never write into the source tree.
    self.assertEqual(src_before, self._ListFiles(self.src))
    return archive_dir, zip_file

  def testNestedDirsParentFirst(self):
    archive_dir, zip_file = self._MakeZip(['a/b', 'a/b/c'])
    self.assertEqual({'a/b/c/f1', 'a/b/f2'}, self._ListFiles(archive_dir))
    with zipfile.ZipFile(zip_file) as z:
      files = [n for n in z.namelist() if not n.endswith('/')]
    self.assertEqual(
      sorted(['archive/a/b/c/f1', 'archive/a/b/f2']), sorted(files)
    )

  def testNestedDirsChildFirst(self):
    archive_dir, _ = self._MakeZip(['a/b/c', 'a/b'])
    # a/b/f2 must not be lost because a/b/c was staged first.
    self.assertEqual({'a/b/c/f1', 'a/b/f2'}, self._ListFiles(archive_dir))

  def testDuplicateEntries(self):
    archive_dir, _ = self._MakeZip(['a/b', 'a/b', 'a/g', 'a/g'])
    self.assertEqual(
      {'a/b/c/f1', 'a/b/f2', 'a/g'}, self._ListFiles(archive_dir)
    )

  def testNestedFileIsStillStripped(self):
    calls = []
    real_run_command = chromium_utils.RunCommand

    def _FakeRunCommand(cmd, *args, **kwargs):
      if cmd[0] == 'strip':
        calls.append(cmd)
        return 0
      return real_run_command(cmd, *args, **kwargs)

    with (
      mock.patch.object(chromium_utils, 'RunCommand', _FakeRunCommand),
      mock.patch.object(chromium_utils, '_WIN_LINK_FUNC', None),
    ):
      archive_dir, _ = self._MakeZip(['a/b', 'a/b/f2'], strip_files=['f2'])
    self.assertEqual([['strip', os.path.join(archive_dir, 'a/b/f2')]], calls)

  def testNestedEntriesWithWindowsLinks(self):
    with mock.patch.object(chromium_utils, '_WIN_LINK_FUNC', _FakeWinLinkFunc):
      archive_dir, _ = self._MakeZip(
        ['a/b/f2', 'a/b/c', 'a/b', 'a/b/f2', 'a', 'a/g']
      )
    self.assertEqual(
      {'a/b/c/f1', 'a/b/f2', 'a/g'}, self._ListFiles(archive_dir)
    )


if __name__ == '__main__':
  unittest.main()
