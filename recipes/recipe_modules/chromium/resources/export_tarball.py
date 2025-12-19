#!/usr/bin/env python3
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
This tool creates a tarball with all the sources, but without .git directories.

It can also remove files which are not strictly required for build, so that
the resulting tarball can be reasonably small (last time it was ~110 MB).

Example usage:

export_tarball.py /foo/bar

The above will create file /foo/bar.tar.xz.
"""

from __future__ import annotations

import optparse
import os
import re
import stat
import subprocess
import sys
import tarfile


nonessential_dirs = (
    'third_party/blink/tools',
    'third_party/blink/web_tests',
    'third_party/hunspell_dictionaries',
    'third_party/hunspell/tests',
    'third_party/jdk/current',
    'third_party/jdk/extras',
    'third_party/liblouis/src/tests/braille-specs',
    'third_party/xdg-utils/tests',
    'v8/test',
)

ESSENTIAL_FILES = (
    'chrome/test/data/webui/i18n_process_css_test.html',
    'chrome/test/data/webui/mojo/foobar.mojom',

    # Allows the orchestrator_all target to work with gn gen
    'v8/test/torque/test-torque.tq',
)

TEST_DIRS = (
    'base/tracing/test/data',
    'chrome/test/data',
    'components/test/data',
    # Some files in content/test/data/ are needed to build content_shell.
    # The subdirectories listed below are not needed, and take up most of
    # the space anyway. (https://crbug.com/40213591)
    'content/test/data/accessibility',
    'content/test/data/gpu',
    'content/test/data/media',
    'courgette/testdata',
    'extensions/test/data',
    'media/test/data',
    'native_client/src/trusted/service_runtime/testdata',
    'testing/libfuzzer/fuzzers/wasm_corpus',
    'third_party/blink/manual_tests',
    'third_party/blink/perf_tests',
    'third_party/boringssl/src/third_party/wycheproof_testvectors',
    'third_party/breakpad/breakpad/src/processor/testdata',
    'third_party/catapult/tracing/test_data',
    'third_party/dawn/test',
    'third_party/devtools-frontend/src/front_end/panels/timeline/fixtures',
    'third_party/expat/src/testdata',
    'third_party/glslang/src/Test',
    'third_party/harfbuzz-ng/src/test',
    'third_party/libc++/src/test/std',
    'third_party/openh264/src/res',
    'third_party/opus/tests/resources',
    'third_party/ots/src/tests/fonts',
    'third_party/rust-src/src/gcc/gcc/testsuite',
    'third_party/rust-src/src/llvm-project/clang/test',
    'third_party/rust-src/src/llvm-project/llvm/test',
    'third_party/screen-ai/linux/resources',
    'third_party/sqlite/fuzz',
    'third_party/sqlite/src/test',
    'third_party/swiftshader/tests/regres',
    'third_party/test_fonts/test_fonts',
    'tools/perf/testdata',
)


# Workaround lack of the exclude parameter in add method in python-2.4.
# TODO(phajdan.jr): remove the workaround when it's not needed on the bot.
class MyTarFile(tarfile.TarFile):
  def set_remove_nonessential_files(self, remove):
    # pylint: disable=attribute-defined-outside-init
    self.__remove_nonessential_files = remove

  def set_verbose(self, verbose):
    # pylint: disable=attribute-defined-outside-init
    self.__verbose = verbose

  def set_src_dir(self, src_dir):
    # pylint: disable=attribute-defined-outside-init
    self.__src_dir = src_dir

  def set_mtime(self, mtime):
    # pylint: disable=attribute-defined-outside-init
    self.__mtime = mtime

  def __report_skipped(self, name):
    if self.__verbose:
      print('D\t%s' % name)

  def __report_added(self, name):
    if self.__verbose:
      print('A\t%s' % name)

  def __filter(self, tar_info):
    tar_info.mtime = self.__mtime
    tar_info.mode |= stat.S_IWUSR
    tar_info.uid = 0
    tar_info.gid = 0
    tar_info.uname = '0'
    tar_info.gname = '0'
    return tar_info

  # pylint: disable=redefined-builtin
  def add(self, name, arcname=None, recursive=True, *, filter=None):
    rel_name = os.path.relpath(name, self.__src_dir)
    file_path, file_name = os.path.split(name)

    if os.path.islink(name) and not os.path.exists(name):
      # Beware of symlinks whose target is nonessential
      self.__report_skipped(name)
      return

    if file_name == '__pycache__' or file_name.endswith('.pyc'):
      self.__report_skipped(name)
      return

    if file_name in ('.svn', 'out'):
      # Since m132 devtools-frontend requires files in node_modules/<module>/out
      # to prevent this happening again we can exclude based on the path
      # rather than explicitly allowlisting
      if 'node_modules' not in file_path:
        self.__report_skipped(name)
        return

    if file_name == '.git':
      # The content of .git subdirs is not reproducible, and must not
      # be depended upon by a tarball build.
      self.__report_skipped(name)
      return

    if self.__remove_nonessential_files:
      # WebKit change logs take quite a lot of space. This saves ~10 MB
      # in a bzip2-compressed tarball.
      if 'ChangeLog' in name:
        self.__report_skipped(name)
        return

      # Preserve GN files, and other potentially critical files, so that
      # `gn gen` can work.
      #
      # Preserve `*.pydeps` files too. `gn gen` reads them to generate build
      # targets, even if those targets themselves are not built
      # (crbug.com/1362021).
      keep_file = (
          re.search(r'\.(gn|gni|grd|grdp|isolate|pydeps)(\.\S+)?$', file_name)
          or rel_name in ESSENTIAL_FILES)

      # Remove contents of non-essential directories.
      if not keep_file:
        if any((rel_name == path or rel_name.startswith(path + '/'))
            for path in (set(nonessential_dirs) | set(TEST_DIRS))) and \
            (os.path.isfile(name) or os.path.islink(name)):
          self.__report_skipped(name)
          return

    self.__report_added(name)
    tarfile.TarFile.add(
        self, name, arcname=arcname, recursive=recursive, filter=self.__filter)


def main(argv):
  parser = optparse.OptionParser()
  parser.add_option("--basename")
  parser.add_option("--remove-nonessential-files",
                    dest="remove_nonessential_files",
                    action="store_true", default=False)
  parser.add_option("--test-data", action="store_true")
  # TODO(phajdan.jr): Remove --xz option when it's not needed for compatibility.
  parser.add_option("--xz", action="store_true")
  parser.add_option("--verbose", action="store_true", default=False)
  parser.add_option("--progress", action="store_true", default=False)
  parser.add_option("--src-dir")
  parser.add_option("--version")

  options, args = parser.parse_args(argv)

  if len(args) != 1:
    print('You must provide only one argument: output file name')
    print('(without .tar.xz extension).')
    return 1

  if not options.version:
    print('A version number must be provided via the --version option.')
    return 1

  if not os.path.exists(options.src_dir):
    print('Cannot find the src directory ' + options.src_dir)
    return 1

  output_fullname = args[0] + '.tar.xz'
  output_basename = options.basename or os.path.basename(args[0])

  tarball = open(output_fullname, 'w')
  xz = subprocess.Popen(
      ['xz', '-T', '0', '-9'] + (['-v'] if options.progress else []) + ['-'],
      stdin=subprocess.PIPE,
      stdout=tarball)

  archive = MyTarFile.open(None, 'w|', xz.stdin)
  archive.set_remove_nonessential_files(options.remove_nonessential_files)
  archive.set_verbose(options.verbose)
  archive.set_src_dir(options.src_dir)

  with open(
      os.path.join(options.src_dir, 'build/util/LASTCHANGE.committime'),
      'r') as f:
    timestamp = int(f.read())
    archive.set_mtime(timestamp)

  try:
    if options.test_data:
      for directory in TEST_DIRS:
        test_dir = os.path.join(options.src_dir, directory)
        if not os.path.isdir(test_dir):
          # A directory may not exist depending on the milestone we're building
          # a tarball for.
          print('"%s" not present; skipping.' % test_dir)
          continue
        archive.add(test_dir,
                    arcname=os.path.join(output_basename, directory))
    else:
      archive.add(options.src_dir, arcname=output_basename)
  finally:
    archive.close()

  xz.stdin.close()

  if xz.wait() != 0:
    print('xz -9 failed!')
    return 1

  tarball.flush()
  tarball.close()

  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
