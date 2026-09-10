# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Functions specific to bots, shared by several scripts."""

from __future__ import absolute_import
from __future__ import print_function

import datetime
import glob
import os
import re
import sys
import tempfile

from common import chromium_utils

# These codes used to distinguish true errors from script warnings.
ERROR_EXIT_CODE = 1
WARNING_EXIT_CODE = 88

# Global variables set by command-line arguments (AddArgs).
_ARGS_GSUTIL_PY_PATH = None


def _GitExe():
  return 'git.bat' if chromium_utils.IsWindows() else 'git'


class _NotGitWorkingCopy(Exception):
  pass


class _NotAnyWorkingCopy(Exception):
  pass


def _GitHash(wc_dir):
  """Finds the current commit hash of the wc_dir."""
  retval, text = chromium_utils.GetStatusOutput(
    [_GitExe(), 'rev-parse', 'HEAD'],
    cwd=wc_dir,
  )
  if retval or 'fatal: Not a git repository' in text:
    raise _NotGitWorkingCopy(wc_dir)
  return text.strip()


def _GetHashOrRevision(wc_dir):
  """Gets the git hash of wc_dir as a string. Throws NotAnyWorkingCopy if the
  wc_dir isn't a git checkout."""
  try:
    return _GitHash(wc_dir)
  except _NotGitWorkingCopy:
    pass
  raise _NotAnyWorkingCopy(wc_dir)


def GetBuildRevisions(src_dir, revision_dir=None):
  """Parses build revisions out of the provided directories.

  Args:
    src_dir: The source directory to be used to check the revision in.
    revision_dir: If provided, this dir will be used for the build revision
      instead of the mandatory src_dir.

  Returns a tuple of the build revision and (optional) WebKit revision.
  NOTICE: These revisions are strings, since they can be both Subversion numbers
  and Git hashes.
  """
  abs_src_dir = os.path.abspath(src_dir)
  if revision_dir:
    revision_dir = os.path.join(abs_src_dir, revision_dir)
    build_revision = _GetHashOrRevision(revision_dir)
  else:
    build_revision = _GetHashOrRevision(src_dir)
  return build_revision


def GetZipFileNames(build_revision):
  base_name = 'full-build-%s' % chromium_utils.PlatformName()
  version_suffix = '_%s' % build_revision
  return base_name, version_suffix


def _GSUtilSetup():
  # Get the path to the gsutil script.
  if _ARGS_GSUTIL_PY_PATH:
    # The `gsutil.py` path was supplied on the command-line. Run this through
    # our local Python interpreter.
    #
    # Note: you should not use sys.executable because this function
    # could be used under vpython, which does not work well (crbug.com/793325).
    gsutil = ['python3', _ARGS_GSUTIL_PY_PATH, '--']
  else:
    # Fall back to local repository 'gsutil' invocation. NOTE that this requires
    # the standard infra checkout layout, namely that 'depot_tools' is checked
    # out one directory above 'build'.
    gsutil = os.path.join(os.path.dirname(__file__), 'gsutil')
    gsutil = os.path.normpath(gsutil)
    if chromium_utils.IsWindows():
      gsutil += '.bat'
    gsutil = [gsutil]

  return gsutil


def _GSUtilGetMetadataField(name, provider_prefix=None):
  """Returns: (str) the metadata field to use with Google Storage

  The Google Storage specification for metadata can be found at:
  https://developers.google.com/storage/docs/gsutil/addlhelp/WorkingWithObjectMetadata
  """
  # Already contains custom provider prefix
  if name.lower().startswith('x-'):
    return name

  # See if it's innately supported by Google Storage
  if name in (
    'Cache-Control',
    'Content-Disposition',
    'Content-Encoding',
    'Content-Language',
    'Content-MD5',
    'Content-Type',
  ):
    return name

  # Add provider prefix
  if not provider_prefix:
    provider_prefix = 'x-goog-meta'
  return '%s-%s' % (provider_prefix, name)


def GSUtilCopy(
  source,
  dest,
  mimetype=None,
  gs_acl=None,
  cache_control=None,
  metadata=None,
  override_gsutil=None,
  add_quiet_flag=False,
  compress=False,
):
  """Copy a file to Google Storage.

  Runs the following command:
    gsutil -h Content-Type:<mimetype> \
           -h Cache-Control:<cache_control> \
        cp -a <gs_acl> file://<filename> <dest>

  Args:
    source: the source URI
    dest: the destination URI
    mimetype: optional value to add as a Content-Type header
    gs_acl: optional value to add as a canned-acl
    cache_control: optional value to set Cache-Control header
    metadata: (dict) A dictionary of string key/value metadata entries to set
        (see `gsutil cp' '-h' option)
    override_gsutil (list): optional argv to run gsutil
    add_quiet_flag: add the -q (quiet) flag when invoking gsutil

  Returns:
    The status code returned from running the generated gsutil command.
  """

  if not source.startswith('gs://') and not source.startswith('file://'):
    source = 'file://' + source
  if not dest.startswith('gs://') and not dest.startswith('file://'):
    dest = 'file://' + dest
  # The setup also sets up some env variables - for now always run that.
  gsutil = _GSUtilSetup()
  # Run the gsutil command. gsutil internally calls command_wrapper, which
  # will try to run the command 10 times if it fails.
  command = list(override_gsutil or gsutil)
  if add_quiet_flag:
    command.append('-q')

  if not metadata:
    metadata = {}
  if mimetype:
    metadata['Content-Type'] = mimetype
  if cache_control:
    metadata['Cache-Control'] = cache_control
  for k, v in sorted(metadata.items(), key=lambda x: x[0]):
    field = _GSUtilGetMetadataField(k)
    param = (field) if v is None else ('%s:%s' % (field, v))
    command += ['-h', param]
  command.extend(['cp'])
  if gs_acl:
    command.extend(['-a', gs_acl])
  if compress:
    command.extend(['-Z'])
  command.extend([source, dest])
  return chromium_utils.RunCommand(command)


def GSUtilCopyFile(
  filename,
  gs_base,
  subdir=None,
  mimetype=None,
  gs_acl=None,
  cache_control=None,
  metadata=None,
  override_gsutil=None,
  dest_filename=None,
  add_quiet_flag=False,
):
  """Copies a file to Google Storage.

  Runs the following command:
    gsutil -h Content-Type:<mimetype> \
        -h Cache-Control:<cache_control> \
        cp -a <gs_acl> file://<filename> <gs_base>/<subdir>/<dest_filename>

  Args:
    filename: the file to upload
    gs_base: the bucket to upload the file to
    subdir: optional subdirectory within the bucket
    mimetype: optional value to add as a Content-Type header
    gs_acl: optional value to add as a canned-acl
    override_gsutil (list): optional argv to run gsutil
    dest_filename: optional destination filename; if not specified, then the
        destination filename will be the source filename without the path
    add_quiet_flag: add the -q (quiet) flag when invoking gsutil

  Returns:
    The status code returned from running the generated gsutil command.
  """

  source = filename
  if not (filename.startswith('gs://') or filename.startswith('file://')):
    source = 'file://' + filename
  dest = gs_base
  if subdir:
    # HACK(nsylvain): We can't use normpath here because it will break the
    # slashes on Windows.
    if subdir == '..':
      dest = os.path.dirname(gs_base)
    else:
      dest = '/'.join([gs_base, subdir])
  if dest_filename is None:
    dest_filename = os.path.basename(filename)
  dest = '/'.join([dest, dest_filename])
  return GSUtilCopy(
    source,
    dest,
    mimetype,
    gs_acl,
    cache_control,
    metadata=metadata,
    override_gsutil=override_gsutil,
    add_quiet_flag=add_quiet_flag,
  )


def _LogAndRemoveFiles(temp_dir, regex_pattern):
  """Removes files in |temp_dir| that match |regex_pattern|.
  This function prints out the name of each directory or filename before
  it deletes the file from disk."""
  regex = re.compile(regex_pattern)
  if not os.path.isdir(temp_dir):
    return
  for dir_item in os.listdir(temp_dir):
    if regex.search(dir_item):
      full_path = os.path.join(temp_dir, dir_item)
      print('Removing leaked temp item: %s' % full_path)
      try:
        if os.path.islink(full_path) or os.path.isfile(full_path):
          os.remove(full_path)
        elif os.path.isdir(full_path):
          chromium_utils.RemoveDirectory(full_path)
        else:
          print('Temp item wasn\'t a file or directory?')
      except OSError as e:
        print(e, file=sys.stderr)
        # Don't fail.


def _RemoveOldSnapshots(desktop):
  """Removes ChromiumSnapshot files more than one day old. Such snapshots are
  created when certain tests timeout (e.g., Chrome Frame integration tests)."""
  # Compute the file prefix of a snapshot created one day ago.
  yesterday = datetime.datetime.now() - datetime.timedelta(1)
  old_snapshot = yesterday.strftime('ChromiumSnapshot%Y%m%d%H%M%S')
  # Collect snapshots at least as old as that one created a day ago.
  to_delete = []
  for snapshot in glob.iglob(os.path.join(desktop, 'ChromiumSnapshot*.png')):
    if os.path.basename(snapshot) < old_snapshot:
      to_delete.append(snapshot)
  # Delete the collected snapshots.
  for snapshot in to_delete:
    print('Removing old snapshot: %s' % snapshot)
    try:
      os.remove(snapshot)
    except OSError as e:
      print(e, file=sys.stderr)


def _RemoveChromeDesktopFiles():
  """Removes Chrome files (i.e. shortcuts) from the desktop of the current user.
  This does nothing if called on a non-Windows platform."""
  if chromium_utils.IsWindows():
    desktop_path = os.environ['USERPROFILE']
    desktop_path = os.path.join(desktop_path, 'Desktop')
    _LogAndRemoveFiles(desktop_path, r'^(Chromium|chrome) \(.+\)?\.lnk$')
    _RemoveOldSnapshots(desktop_path)


def _RemoveJumpListFiles():
  """Removes the files storing jump list history.
  This does nothing if called on a non-Windows platform."""
  if chromium_utils.IsWindows():
    custom_destination_path = os.path.join(
      os.environ['USERPROFILE'],
      'AppData',
      'Roaming',
      'Microsoft',
      'Windows',
      'Recent',
      'CustomDestinations',
    )
    _LogAndRemoveFiles(custom_destination_path, '.+')


def RemoveChromeTemporaryFiles():
  """A large hammer to nuke what could be leaked files from unittests or
  files left from a unittest that crashed, was killed, etc."""
  # NOTE: print out what is cleaned up so the bots don't timeout if
  # there is a lot to cleanup and also se we see the leaks in the
  # build logs.
  # At some point a leading dot got added, support with and without it.
  kLogRegex = r'^\.?(com\.google\.Chrome|org\.chromium)\.'
  if chromium_utils.IsWindows():
    _RemoveChromeDesktopFiles()
    _RemoveJumpListFiles()
  elif chromium_utils.IsLinux():
    _LogAndRemoveFiles(tempfile.gettempdir(), kLogRegex)
    _LogAndRemoveFiles('/dev/shm', kLogRegex)
  elif chromium_utils.IsMac():
    nstempdir_path = '/usr/local/libexec/nstempdir'
    if os.path.exists(nstempdir_path):
      ns_temp_dir = chromium_utils.GetCommandOutput([nstempdir_path]).strip()
      if ns_temp_dir:
        _LogAndRemoveFiles(ns_temp_dir, kLogRegex)
    for i in ('Chromium', 'Google Chrome'):
      # Remove dumps.
      crash_path = '%s/Library/Application Support/%s/Crash Reports' % (
        os.environ['HOME'],
        i,
      )
      _LogAndRemoveFiles(crash_path, r'^.+\.dmp$')
  else:
    raise NotImplementedError(
      'Platform "%s" is not currently supported.' % sys.platform
    )


def AddArgs(parser):
  """Adds bot_utils common arguments to the supplied argparse parser.

  Args:
      parser (argparse.ArgumentParser): The argument parser to augment.

  Returns: callable(args)
      A callback function that should be invoked with the parsed args. This
      completes the processing and loads the result of the parsing into
      bot_utils.
  """
  group = parser.add_argument_group(title='Common `bot_utils.py` Options')
  group.add_argument(
    '--bot-utils-gsutil-py-path',
    metavar='PATH',
    help='The path to the `gsutil.py` command to use for Google Storage '
    'operations. This file lives in the <depot_tools> repository.',
  )

  return _AddArgsCallback


def _AddArgsCallback(opts):
  """
  Internal callback supplied by AddArgs. Designed to work with
  both argparse and optparse results.
  """
  global _ARGS_GSUTIL_PY_PATH
  _ARGS_GSUTIL_PY_PATH = opts.bot_utils_gsutil_py_path
