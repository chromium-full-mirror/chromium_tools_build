#!/usr/bin/env python3
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""A tool to extract a build, executed by a tester builder."""

import optparse
import os
import shutil
import sys
import traceback

# Add build/recipes and build/scripts.
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(THIS_DIR, '..', 'recipes')))
sys.path.insert(0, os.path.abspath(os.path.join(THIS_DIR, '..', 'scripts')))

from common import chromium_utils
import bot_utils
import build_directory


class ExtractHandler:
  def __init__(self, url, archive_name, gsutil_py_path):
    self.url = url
    self.archive_name = archive_name
    self.gsutil_py_path = gsutil_py_path

  def download(self):
    override_gsutil = [sys.executable, self.gsutil_py_path]
    status = bot_utils.GSUtilCopy(
      self.url, '.', override_gsutil=override_gsutil
    )
    if 0 != status:
      return False
    try:
      shutil.move(os.path.basename(self.url), self.archive_name)
    except OSError:
      os.remove(self.archive_name)
      shutil.move(os.path.basename(self.url), self.archive_name)
    return True


def GetBuildUrl(options, build_revision):
  """Compute the url to download the build from.  This will use as a base
  string, in order of preference:
  0) options.build_archive_url
  1) options.build_url
  2) options.build_properties.build_url
  3) build url constructed from build_properties.  This last type of
     construction is not compatible with the 'force build' button.

  Args:
    options: options object as specified by parser below.
    build_revision: Revision for the build.
  """
  if options.build_archive_url:
    return options.build_archive_url, None

  base_filename, version_suffix = bot_utils.GetZipFileNames(build_revision)

  url = options.build_url
  if url[-4:] != '.zip':  # assume filename not specified
    # Append the filename to the base URL. First strip any trailing slashes.
    url = url.rstrip('/')
    url = f'{url}/{base_filename}.zip'
  archive_name = url.split('/')[-1]
  versioned_url = url.replace('.zip', version_suffix + '.zip')
  return versioned_url, archive_name


def real_main(options):
  """Download a build, extract it to build\\BuildDir\\full-build-win32
  and rename it to build\\BuildDir\\Target
  """
  abs_build_dir = os.path.abspath(
    build_directory.GetBuildOutputDirectory(options.src_dir)
  )
  target_build_output_dir = os.path.join(abs_build_dir, options.target)

  # Generic name for the archive.
  archive_name = 'full-build-%s.zip' % chromium_utils.PlatformName()

  # Just take the zip off the name for the output directory name.
  output_dir = os.path.join(abs_build_dir, archive_name.replace('.zip', ''))

  src_dir = os.path.dirname(abs_build_dir)
  if not options.build_revision and not options.build_archive_url:
    build_revision = bot_utils.GetBuildRevisions(src_dir)
  else:
    build_revision = options.build_revision
  url, archive_name = GetBuildUrl(options, build_revision)
  if archive_name is None:
    archive_name = 'build.zip'

  if not url.startswith('gs://'):
    print(
      f'cannot extract build from {url}, only Google Storage URLs are supported'
    )
    return bot_utils.ERROR_EXIT_CODE

  handler = ExtractHandler(
    url=url,
    archive_name=archive_name,
    gsutil_py_path=options.gsutil_py_path,
  )

  # We try to download and extract 3 times.
  for tries in range(1, 4):
    print('Try %d: Fetching build from %s...' % (tries, url))

    # If the url is valid, we download the file.
    if not handler.download():
      return bot_utils.ERROR_EXIT_CODE

    print('Extracting build %s to %s...' % (archive_name, abs_build_dir))
    try:
      chromium_utils.RemoveDirectory(target_build_output_dir)
      chromium_utils.ExtractZip(archive_name, abs_build_dir)
      # For Chrome builds, the build will be stored in chrome-win32.
      if 'full-build-win32' in output_dir:
        chrome_dir = output_dir.replace('full-build-win32', 'chrome-win32')
        if os.path.exists(chrome_dir):
          output_dir = chrome_dir

      print(
        'Moving build from %s to %s' % (output_dir, target_build_output_dir)
      )
      shutil.move(output_dir, target_build_output_dir)
    except (OSError, IOError, chromium_utils.ExternalError):
      print('Failed to extract the build.')
      # Print out the traceback in a nice format
      traceback.print_exc()
      # Try again...
      continue

    return 0

  # If we get here, that means that it failed 3 times. We return a failure.
  return bot_utils.ERROR_EXIT_CODE


def main():
  option_parser = optparse.OptionParser()

  option_parser.add_option(
    '--target', help='build target to archive (Debug or Release)'
  )
  option_parser.add_option(
    '--src-dir', default='src', help='path to the top-level sources directory'
  )
  option_parser.add_option('--build-dir', help='ignored')
  option_parser.add_option(
    '--build-url', help='Base url where to find the build to extract'
  )
  option_parser.add_option(
    '--build-archive-url', help='Exact url where to find the build to extract'
  )
  option_parser.add_option(
    '--build_revision',
    help='Revision of the build that is being '
    'archived. Overrides the revision found on '
    'the local disk',
  )
  option_parser.add_option(
    '--revision-dir',
    help=(
      'Directory path that shall be used to decide '
      'the revision number for the archive, '
      'relative to the src/ dir.'
    ),
  )
  option_parser.add_option('--build-output-dir', help='ignored')
  option_parser.add_option(
    '--gsutil-py-path', help='Specify path to gsutil.py script.'
  )
  chromium_utils.AddPropertiesOptions(option_parser)

  options, args = option_parser.parse_args()
  if args:
    print('Unknown options: %s' % args)
    return 1
  if not options.gsutil_py_path:
    print('--gsutil-py-path must be specified')
    return 1

  if not options.build_url:
    options.build_url = options.build_properties.get('build_url')
  if not options.target:
    options.target = options.build_properties.get('target', 'Release')
  options.src_dir = (
    options.build_properties.get('extract_build_src_dir') or options.src_dir
  )

  if not options.build_archive_url and not options.build_url:
    print('At least one of --build-archive-url or --build-url must be passed')
    return 1

  return real_main(options)


if '__main__' == __name__:
  sys.exit(main())
