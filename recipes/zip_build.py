#!/usr/bin/env python3
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Creates a zip file in the staging dir with the result of a compile.
It can be sent to other machines for testing.
"""

import csv
import fnmatch
import glob
import json
import optparse
import os
import re
import shutil
import stat
import sys
import tempfile

# Add build/recipes and build/scripts.
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(THIS_DIR, '..', 'recipes')))
sys.path.insert(0, os.path.abspath(os.path.join(THIS_DIR, '..', 'scripts')))

from common import chromium_utils
import bot_utils
import build_directory

_GS_PROTOCOL_PREFIX = 'gs://'

# Mojo JS bindings path relative to the build directory.
MOJO_BINDINGS_PATH = 'gen/mojo/public/js/mojo_bindings.js'

# A list of mojom search paths relative to the build directory.
MOJOM_SEARCH_DIRS = [
  'gen/components',
  'gen/content/test/data',
  'gen/device',
  'gen/gpu/ipc/common/',
  'gen/media/capture/mojo',
  'gen/media/mojo/interfaces/',
  'gen/mojo',
  'gen/services',
  'gen/skia/public/interfaces',
  'gen/third_party/WebKit/public',
  'gen/third_party/WebKit/Source',
  'gen/third_party/blink/public',
  'gen/third_party/blink/renderer',
  'gen/url/mojo',
  'gen/ui',
]

# Layout test data directory relative to the build directory.
LAYOUT_TEST_DATA_DIR = 'gen/layout_test_data'


class StagingError(Exception):
  pass


def CopyDebugCRT(build_dir):
  # Copy the relevant CRT DLLs to |build_dir|. We copy DLLs from all versions
  # of VS installed to make sure we have the correct CRT version, unused DLLs
  # should not conflict with the others anyways.
  crt_dlls = glob.glob(
    'C:\\Program Files (x86)\\Microsoft Visual Studio *\\VC\\redist\\'
    'Debug_NonRedist\\x86\\Microsoft.*.DebugCRT\\*.dll'
  )
  for dll in crt_dlls:
    shutil.copy(dll, build_dir)


def GetRecentBuildsByBuildNumber(zip_list, zip_base, zip_ext, prune_limit):
  # Build an ordered list of build numbers we have zip files for.
  regexp = re.compile(zip_base + '_([0-9]+)(_old)?' + zip_ext)
  build_list = []
  for x in zip_list:
    regexp_match = regexp.match(os.path.basename(x))
    if regexp_match:
      build_list.append(int(regexp_match.group(1)))
  # Since we match both ###.zip and ###_old.zip, bounce through a set and back
  # to a list to get an order list of build numbers.
  build_list = list(set(build_list))
  build_list.sort()
  # Only keep the last prune_limit number (that means we could have
  # 2*prune_limit due to _old files if someone forced a respin of
  # every single one)
  saved_build_list = build_list[-prune_limit:]
  ordered_asc_by_build_number_list = []
  for saved_build in saved_build_list:
    recent_name = zip_base + ('_%d' % saved_build) + zip_ext
    ordered_asc_by_build_number_list.append(recent_name)
    ordered_asc_by_build_number_list.append(
      recent_name.replace(zip_ext, '_old' + zip_ext)
    )
  return ordered_asc_by_build_number_list


def GetRecentBuildsByModificationTime(zip_list, prune_limit):
  """Return the prune_limit most recent builds by modification time."""
  # Get the modification times for all of the entries in zip_list.
  mtimes_to_files = {}
  for zip_file in zip_list:
    mtime = int(os.stat(zip_file).st_mtime)
    mtimes_to_files.setdefault(mtime, [])
    mtimes_to_files[mtime].append(zip_file)
  # Order all files in our list by modification time.
  mtimes_to_files_keys = list(mtimes_to_files.keys())
  mtimes_to_files_keys.sort()
  ordered_asc_by_mtime_list = []
  for key in mtimes_to_files_keys:
    ordered_asc_by_mtime_list.extend(mtimes_to_files[key])
  # Return the most recent 10 builds.
  return ordered_asc_by_mtime_list[-prune_limit:]


def FileRegexWhitelist(options):
  if chromium_utils.IsWindows() and options.target == 'Release':
    # Special case for chrome. Add back all the chrome*.pdb files to the list.
    # Also add browser_test*.pdb, ui_tests.pdb and ui_tests.pdb.
    # TODO(nsylvain): This should really be defined somewhere else.
    return (
      r'^(chrome[_.]dll|chrome[_.]exe'
      # r'|browser_test.+|unit_tests'
      r')\.pdb$'
    )

  return '$NO_FILTER^'


def FileRegexBlacklist(options):
  if chromium_utils.IsWindows():
    return r'^.+\.(rc|res|lib|exp|ilk|7z|([pP]recompile\.h\.pch.*))$'
  if chromium_utils.IsMac():
    # The static libs are just built as intermediate targets, and we don't
    # need to pull the dSYMs over to the testers most of the time (except for
    # the memory tools).
    if options.package_dsym_files:
      return r'^.+\.(a)$'
    return r'^.+\.(a|dSYM)$'
  if chromium_utils.IsLinux():
    # object files, archives, and gcc (make build) dependency info.
    return r'^.+\.(o|a|d)$'

  return '$NO_FILTER^'


def _MojomFiles(build_dir, suffixes):
  """Lists all mojom files that need to be included in the archive.

  Args:
    build_dir: The build directory.

  Returns:
    A list of mojom file paths which are relative to the build
    directory.
  """
  mojom_files = []
  for walk_dir in MOJOM_SEARCH_DIRS:
    walk_dir = os.path.join(build_dir, walk_dir)
    for path, _, files in os.walk(walk_dir):
      rel_path = os.path.relpath(path, build_dir)
      for suffix in suffixes or []:
        for mojom_file in fnmatch.filter(files, '*%s' % suffix):
          mojom_files.append(os.path.join(rel_path, mojom_file))
  return mojom_files


def _LayoutTestFiles(build_dir):
  """Lists all layout test data files that need to be included in the archive.

  Args:
    build_dir: The build directory.

  Returns:
    A list of file paths which are relative to the build directory.
  """
  results = []
  layout_test_data_dir = os.path.join(build_dir, LAYOUT_TEST_DATA_DIR)
  for path, _, files in os.walk(layout_test_data_dir):
    rel_path = os.path.relpath(path, build_dir)
    for entry in files:
      results.append(os.path.join(rel_path, entry))
  return results


def WriteRevisionFile(dirname, build_revision):
  """Writes a file containing revision number to given directory.
  Replaces the target file in place.

  Args:
    dirname: Directory to write the file in.
    build_revision: Revision number or hash.

  Returns: The path of the written file.
  """
  try:
    # Script only works on python 2.6
    # pylint: disable=E1123
    tmp_revision_file = tempfile.NamedTemporaryFile(
      mode='w', dir=dirname, delete=False
    )
    tmp_revision_file.write('%s' % build_revision)
    tmp_revision_file.close()
    chromium_utils.MakeWorldReadable(tmp_revision_file.name)
    dest_path = os.path.join(
      dirname, chromium_utils.FULL_BUILD_REVISION_FILENAME
    )
    shutil.move(tmp_revision_file.name, dest_path)
    return dest_path
  except IOError:
    print('Writing to revision file in %s failed.' % dirname)


def MakeUnversionedArchive(
  build_dir, staging_dir, zip_file_list, zip_file_name, strip_files=None
):
  """Creates an unversioned full build archive.
  Returns the path of the created archive."""
  # Prevents having zip_file_list to contain duplicates
  zip_file_list = list(set(zip_file_list))
  (zip_dir, zip_file) = chromium_utils.MakeZip(
    staging_dir,
    zip_file_name,
    zip_file_list,
    build_dir,
    raise_error=True,
    strip_files=strip_files,
  )

  chromium_utils.RemoveDirectory(zip_dir)
  if not os.path.exists(zip_file):
    raise StagingError('Failed to make zip package %s' % zip_file)
  chromium_utils.MakeWorldReadable(zip_file)

  # Report the size of the zip file to help catch when it gets too big and
  # can cause bot failures from timeouts during downloads to testers.
  zip_size = os.stat(zip_file)[stat.ST_SIZE]
  print('Zip file is %ld bytes' % zip_size)

  return zip_file


def MakeVersionedArchive(zip_file, file_suffix, options):
  """Takes a file name, e.g. /foo/bar.zip and an extra suffix, e.g. _baz,
  and copies (or hardlinks) the file to /foo/bar_baz.zip.

  Returns: A tuple containing three elements: the base filename, the extension
     and the full versioned filename."""
  zip_template = os.path.basename(zip_file)
  zip_base, zip_ext = os.path.splitext(zip_template)
  # Create a versioned copy of the file.
  versioned_file = zip_file.replace(zip_ext, file_suffix + zip_ext)
  # Allow for overriding the name of the file based on the given upload url.
  if options.use_build_url_name:
    new_build_url, new_archive_name = options.build_url.rsplit('/', 1)
    if new_archive_name and new_archive_name.endswith('.zip'):
      options.build_url = new_build_url
      versioned_file = zip_file.replace(zip_template, new_archive_name)
  if os.path.exists(versioned_file):
    # This file already exists. Maybe we are doing a clobber build at the same
    # revision. We can move this file away.
    old_file = versioned_file.replace(zip_ext, '_old' + zip_ext)
    chromium_utils.MoveFile(versioned_file, old_file)
  if chromium_utils.IsWindows():
    shutil.copyfile(zip_file, versioned_file)
  else:
    os.link(zip_file, versioned_file)
  chromium_utils.MakeWorldReadable(versioned_file)
  print('Created versioned archive', versioned_file)
  return (zip_base, zip_ext, versioned_file)


def UploadToGoogleStorage(
  versioned_file, revision_file, build_url, gs_acl, gsutil_py_path
):
  override_gsutil = [sys.executable, gsutil_py_path]

  if bot_utils.GSUtilCopyFile(
    versioned_file, build_url, gs_acl=gs_acl, override_gsutil=override_gsutil
  ):
    raise chromium_utils.ExternalError(
      'gsutil returned non-zero status when uploading %s to %s!'
      % (versioned_file, build_url)
    )
  print('Successfully uploaded %s to %s' % (versioned_file, build_url))

  # The file showing the latest uploaded revision must be named LAST_CHANGE
  # locally since that filename is used in the GS bucket as well.
  last_change_file = os.path.join(os.path.dirname(revision_file), 'LAST_CHANGE')
  shutil.copy(revision_file, last_change_file)
  if bot_utils.GSUtilCopyFile(
    last_change_file, build_url, gs_acl=gs_acl, override_gsutil=override_gsutil
  ):
    raise chromium_utils.ExternalError(
      'gsutil returned non-zero status when uploading %s to %s!'
      % (last_change_file, build_url)
    )
  print('Successfully uploaded %s to %s' % (last_change_file, build_url))
  os.remove(last_change_file)
  return '/'.join([build_url, os.path.basename(versioned_file)])


def PruneOldArchives(staging_dir, zip_base, zip_ext, prune_limit):
  """Removes old archives so that we don't exceed disk space."""
  zip_list = glob.glob(os.path.join(staging_dir, zip_base + '_*' + zip_ext))
  saved_zip_list = GetRecentBuildsByBuildNumber(
    zip_list, zip_base, zip_ext, prune_limit
  )
  saved_mtime_list = GetRecentBuildsByModificationTime(zip_list, prune_limit)

  # Prune zip files not matched by the whitelists above.
  for zip_file in zip_list:
    if zip_file not in saved_zip_list and zip_file not in saved_mtime_list:
      print('Pruning zip %s.' % zip_file)
      chromium_utils.RemoveFile(staging_dir, zip_file)


class PathMatcher:
  """Generates a matcher which can be used to filter file paths."""

  def __init__(self, options):

    def CommaStrParser(val):
      return [f.strip() for f in next(csv.reader([val]))]

    self.inclusions = CommaStrParser(options.include_files)
    self.exclusions = (
      CommaStrParser(options.exclude_files) + chromium_utils.FileExclusions()
    )
    self.regex_whitelist = FileRegexWhitelist(options)
    self.regex_blacklist = FileRegexBlacklist(options)
    self.exclude_unmatched = options.exclude_unmatched
    self.exclude_extra = options.exclude_extra
    self.custom_whitelist = options.whitelist

  def __str__(self):
    return '\n  '.join(
      [
        'Zip rules',
        'Inclusions: %s' % self.inclusions,
        'Exclusions: %s' % self.exclusions,
        "Whitelist regex: '%s'" % self.regex_whitelist,
        "Blacklist regex: '%s'" % self.regex_blacklist,
        'Zip unmatched files: %s' % (not self.exclude_unmatched),
        'Exclude extra: %s' % self.exclude_extra,
        "Custom Whitelist regex: '%s'" % self.custom_whitelist,
      ]
    )

  def Match(self, filename):
    for p in self.inclusions:
      if fnmatch.fnmatch(filename, p):
        return True
    for p in self.exclusions:
      if fnmatch.fnmatch(filename, p):
        return False
    if self.custom_whitelist and re.match(self.custom_whitelist, filename):
      return True
    if self.exclude_extra:
      return False
    if re.match(self.regex_whitelist, filename):
      return True
    if re.match(self.regex_blacklist, filename):
      return False
    return not self.exclude_unmatched


def Archive(options):
  build_dir = build_directory.GetBuildOutputDirectory(options.src_dir)
  build_dir = os.path.abspath(os.path.join(build_dir, options.target))

  staging_dir = options.staging_dir
  if not os.path.exists(staging_dir):
    os.makedirs(staging_dir)
  chromium_utils.MakeParentDirectoriesWorldReadable(staging_dir)
  if not options.build_revision:
    build_revision = bot_utils.GetBuildRevisions(
      options.src_dir, options.revision_dir
    )
  else:
    build_revision = options.build_revision

  unversioned_base_name, version_suffix = bot_utils.GetZipFileNames(
    build_revision
  )

  print('Full Staging in %s' % staging_dir)
  print('Build Directory %s' % build_dir)

  # Include the revision file in tarballs
  WriteRevisionFile(build_dir, build_revision)

  # Copy the crt files if necessary.
  if options.target == 'Debug' and chromium_utils.IsWindows():
    CopyDebugCRT(build_dir)

  path_filter = PathMatcher(options)
  print(path_filter)

  # Build the list of files to archive.
  zip_file_list = []
  for root, dirs, files in os.walk(build_dir):

    def rel(f):
      return os.path.relpath(os.path.join(root, f), build_dir)

    zip_file_list += [rel(f) for f in files if path_filter.Match(rel(f))]

    # For some reason os.walk returns symbolic links that point to directories
    # in dirs and not files, so we need to handle any symlinks in dirs as if
    # they appeared in files instead.
    def islink(f):
      return os.path.islink(os.path.join(root, f))

    zip_file_list += [
      rel(d) for d in dirs if islink(d) and path_filter.Match(rel(d))
    ]
    dirs[:] = [d for d in dirs if not islink(d) and path_filter.Match(rel(d))]

  # Include mojo public JS library.
  if os.path.exists(os.path.join(build_dir, MOJO_BINDINGS_PATH)):
    print('Include mojo public JS library: %s' % MOJO_BINDINGS_PATH)
    zip_file_list.append(MOJO_BINDINGS_PATH)

  # TODO(yzshen): Switch layout tests to use files from 'gen/layout_test_data'
  # and remove this.
  mojom_files = _MojomFiles(build_dir, ['.mojom.js', '_mojom.py'])
  print('Include mojom files: %s' % mojom_files)
  zip_file_list.extend(mojom_files)

  layout_test_data_files = _LayoutTestFiles(build_dir)
  print('Include layout test data: %s' % layout_test_data_files)
  zip_file_list.extend(layout_test_data_files)

  zip_file = MakeUnversionedArchive(
    build_dir,
    staging_dir,
    zip_file_list,
    unversioned_base_name,
    strip_files=options.strip_files,
  )

  zip_base, zip_ext, versioned_file = MakeVersionedArchive(
    zip_file, version_suffix, options
  )

  prune_limit = 10
  if options.build_url.startswith('gs://'):
    # Don't keep builds lying around when uploading them to google storage.
    prune_limit = 3
  PruneOldArchives(staging_dir, zip_base, zip_ext, prune_limit=prune_limit)

  # Update the latest revision file in the staging directory
  # to allow testers to figure out the latest packaged revision
  # without downloading tarballs.
  revision_file = WriteRevisionFile(staging_dir, build_revision)

  return versioned_file, revision_file


def Upload(options, versioned_file, revision_file):
  zip_url = UploadToGoogleStorage(
    versioned_file,
    revision_file,
    options.build_url,
    options.gs_acl,
    options.gsutil_py_path,
  )
  return {
    'storage_url': 'https://storage.cloud.google.com/%s/%s'
    % (
      options.build_url[len(_GS_PROTOCOL_PREFIX) :],
      os.path.basename(versioned_file),
    ),
    'zip_url': zip_url,
  }


def AddOptions(option_parser):
  option_parser.add_option(
    '--target',
    help='build target to archive (Debug or Release)',
    default='Release',
  )
  option_parser.add_option(
    '--src-dir', default='src', help='path to the top-level sources directory'
  )
  option_parser.add_option('--build-dir', help='ignored')
  option_parser.add_option(
    '--exclude-files',
    default='',
    help='Comma separated list of files that should '
    'always be excluded from the zip.',
  )
  option_parser.add_option(
    '--include-files',
    default='',
    help='Comma separated list of files that should '
    'always be included in the zip.',
  )
  option_parser.add_option(
    '--whitelist', default='', help='Custom regex whitelist to include files'
  )
  option_parser.add_option(
    '--exclude-extra',
    action='store_true',
    default=False,
    help='Only includes include file listand regex whitelist match provided',
  )
  option_parser.add_option(
    '--revision-dir',
    help='Directory path that shall be used to decide '
    'the revision number for the archive, '
    'relative to --src-dir',
  )
  option_parser.add_option(
    '--build_revision',
    help='The revision the archive should be at. '
    'Overrides the revision found on disk.',
  )
  option_parser.add_option(
    '--exclude-unmatched',
    action='store_true',
    help='Exclude all files not matched by a whitelist',
  )
  option_parser.add_option(
    '--build-url',
    default='',
    help=(
      'Optional URL to which to upload build '
      '(overrides build_url build property)'
    ),
  )
  option_parser.add_option(
    '--use-build-url-name',
    action='store_true',
    help=('Use the filename given in --build-url insteadof generating one.'),
  )
  option_parser.add_option(
    '--package-dsym-files',
    action='store_true',
    default=False,
    help='Add also dSYM files.',
  )
  option_parser.add_option('--gs-acl')
  option_parser.add_option(
    '--strip-files',
    default='',
    help='Comma separated list of files that should '
    'be stripped of symbols in the zip.',
  )
  option_parser.add_option(
    '--json-urls',
    help=(
      'Path to json file containing uploaded '
      'archive urls. If this is omitted then '
      'the urls will be emitted as buildbot '
      'annotations.'
    ),
  )
  option_parser.add_option(
    '--staging-dir',
    help='Directory to use for staging the archives. This flag is required',
  )
  option_parser.add_option(
    '--gsutil-py-path', help='Specify path to gsutil.py script.'
  )


def main(argv):
  option_parser = optparse.OptionParser()
  AddOptions(option_parser)
  chromium_utils.AddPropertiesOptions(option_parser)

  options, args = option_parser.parse_args(argv)
  if not options.gsutil_py_path:
    print('--gsutil-py-path must be specified')
    return 1
  if not options.staging_dir:
    print('--staging-dir must be specified')
    return 1
  if not options.build_url:
    print('--build-url must be specified')
    return 1
  if not options.build_url.startswith(_GS_PROTOCOL_PREFIX):
    print(
      f'value for --build-url must start with "{_GS_PROTOCOL_PREFIX}",'
      f' got "{options.build_url}"'
    )
    return 1

  if options.strip_files:
    options.strip_files = options.strip_files.split(',')
  # When option_parser is passed argv as a list, it can return the caller as
  # first unknown arg.  So throw a warning if we have two or more unknown
  # arguments.
  if args[1:]:
    print('Warning -- unknown arguments' % args[1:])

  versioned_file, revision_file = Archive(options)
  urls = Upload(options, versioned_file, revision_file)
  if options.json_urls:  # we need to dump json
    with open(options.json_urls, 'w') as json_file:
      json.dump(urls, json_file)
  return 0


if '__main__' == __name__:
  sys.exit(main(sys.argv))
