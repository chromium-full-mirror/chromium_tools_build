# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Set of basic operations/utilities that are used by the build."""

from __future__ import print_function

import errno
import fnmatch
import io
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

_WIN_LINK_FUNC = None
try:
  if sys.platform.startswith('win'):
    import ctypes

    # There's 4 possibilities on Windows for links:
    # 1. Symbolic file links;
    # 2. Symbolic directory links;
    # 3. Hardlinked files;
    # 4. Junctioned directories.
    # (Hardlinked directories don't really exist.)
    #
    # 7-Zip does not handle symbolic file links as we want (it puts the
    # content of the link, not what it refers to, and reports "CRC Error" on
    # extraction). It does work as expected for symbolic directory links.
    # Because the majority of the large files are in the root of the staging
    # directory, we do however need to handle file links, so we do this with
    # hardlinking. Junctioning requires a huge whack of code, so we take the
    # slightly odd tactic of using #2 and #3, but not #1 and #4. That is,
    # hardlinks for files, but symbolic links for directories.
    def _WIN_LINK_FUNC(src, dst):
      print('linking %s -> %s' % (src, dst))
      if os.path.isdir(src):
        if sys.version_info.major == 2:
          if not ctypes.windll.kernel32.CreateSymbolicLinkA(
            str(dst), str(os.path.abspath(src)), 1
          ):
            raise ctypes.WinError()
        else:
          os.symlink(os.path.abspath(src), dst, target_is_directory=True)
      else:
        if sys.version_info.major == 2:
          if not ctypes.windll.kernel32.CreateHardLinkA(str(dst), str(src), 0):
            raise ctypes.WinError()
        else:
          os.link(src, dst)
except ImportError:
  # If we don't have ctypes or aren't on Windows, leave _WIN_LINK_FUNC as None.
  pass


# Local errors.
class MissingArgument(Exception):
  pass


class PathNotFound(Exception):
  pass


class ExternalError(Exception):
  pass


def IsWindows():
  return sys.platform == 'cygwin' or sys.platform.startswith('win')


def IsLinux():
  return sys.platform.startswith('linux')


def IsMac():
  return sys.platform.startswith('darwin')


# For chromeos we need to end up with a different platform name, but the
# scripts use the values like sys.platform for both the build target and
# and the running OS, so this gives us a back door that can be hit to
# force different naming then the default for some of the chromeos build
# steps.
override_platform_name = None


def OverridePlatformName(name):
  """Sets the override for PlatformName()"""
  global override_platform_name
  override_platform_name = name


def PlatformName():
  """Return a string to be used in paths for the platform."""
  if override_platform_name:
    return override_platform_name
  if IsWindows():
    return 'win32'
  if IsLinux():
    return 'linux'
  if IsMac():
    return 'mac'
  raise NotImplementedError('Unknown platform "%s".' % sys.platform)


# Name of the file (inside the packaged build) containing revision number
# of that build. Also used for determining the latest packaged build.
FULL_BUILD_REVISION_FILENAME = 'FULL_BUILD_REVISION'


def MeanAndStandardDeviation(data):
  """Calculates mean and standard deviation for the values in the list.

  Args:
    data: list of numbers

  Returns:
    Mean and standard deviation for the numbers in the list.
  """
  n = len(data)
  if n == 0:
    return 0.0, 0.0
  mean = float(sum(data)) / n
  variance = sum([(element - mean) ** 2 for element in data]) / n
  return mean, math.sqrt(variance)


def HistogramPercentiles(histogram, percentiles):
  if not 'buckets' in histogram or not 'count' in histogram:
    return []
  computed_percentiles = _ComputePercentiles(
    histogram['buckets'], histogram['count'], percentiles
  )
  output = []
  for p in computed_percentiles:
    output.append({'percentile': p, 'value': computed_percentiles[p]})
  return output


def GeomMeanAndStdDevFromHistogram(histogram):
  if not 'buckets' in histogram:
    return 0.0, 0.0
  count = 0
  sum_of_logs = 0
  for bucket in histogram['buckets']:
    if 'high' in bucket:
      bucket['mean'] = (bucket['low'] + bucket['high']) / 2.0
    else:
      bucket['mean'] = bucket['low']
    if bucket['mean'] > 0:
      sum_of_logs += math.log(bucket['mean']) * bucket['count']
      count += bucket['count']

  if count == 0:
    return 0.0, 0.0

  sum_of_squares = 0
  geom_mean = math.exp(sum_of_logs / count)
  for bucket in histogram['buckets']:
    if bucket['mean'] > 0:
      sum_of_squares += (bucket['mean'] - geom_mean) ** 2 * bucket['count']
  return geom_mean, math.sqrt(sum_of_squares / count)


def _LinearInterpolate(x0, target, x1, y0, y1):
  """Perform linear interpolation to estimate an intermediate value.

  We assume for some F, F(x0) == y0, and F(x1) == z1.

  We return an estimate for what F(target) should be, using linear
  interpolation.

  Args:
    x0: (Float) A location at which some function F() is known.
    target: (Float) A location at which we need to estimate F().
    x1: (Float) A second location at which F() is known.
    y0: (Float) The value of F(x0).
    y1: (Float) The value of F(x1).

  Returns:
    (Float) The estimated value of F(target).
  """
  if x0 == x1:
    return (y0 + y1) / 2
  return (y1 - y0) * (target - x0) / (x1 - x0) + y0


def _BucketInterpolate(
  last_percentage, target, next_percentage, bucket_min, bucket_max
):
  """Estimate a minimum which should have the target % of samples below it.

  We do linear interpolation only if last_percentage and next_percentage are
  adjacent, and hence we are in a linear section of a histogram. Once they
  spread further apart we generally get exponentially broader buckets, and we
  need to interpolate in the log domain (and exponentiate our result).

  Args:
    last_percentage: (Float) This is the percentage of samples below bucket_min.
    target: (Float) A percentage for which we need an estimated bucket.
    next_percentage: (Float) This is the percentage of samples below bucket_max.
    bucket_min: (Float) This is the lower value for samples in a bucket.
    bucket_max: (Float) This exceeds the upper value for samples.

  Returns:
    (Float) An estimate of what bucket cutoff would have probably had the target
        percentage.
  """
  log_domain = False
  if bucket_min + 1.5 < bucket_max and bucket_min > 0:
    log_domain = True
    bucket_min = math.log(bucket_min)
    bucket_max = math.log(bucket_max)
  result = _LinearInterpolate(
    last_percentage, target, next_percentage, bucket_min, bucket_max
  )
  if log_domain:
    result = math.exp(result)
  return result


def _ComputePercentiles(buckets, total, percentiles):
  """Compute percentiles for the given histogram.

  Returns estimates for the bucket cutoffs that would probably have the taret
  percentiles.

  Args:
    buckets: (List) A list of buckets representing the histogram to analyze.
    total: (Float) The total number of samples in the histogram.
    percentiles: (Tuple) The percentiles we are interested in.

  Returns:
    (Dictionary) Map from percentiles to bucket cutoffs.
  """
  if not percentiles:
    return {}
  current_count = 0
  current_percentage = 0
  next_percentile_index = 0
  result = {}
  for bucket in buckets:
    if bucket['count'] > 0:
      current_count += bucket['count']
      old_percentage = current_percentage
      current_percentage = float(current_count) / total

      # Check whether we passed one of the percentiles we're interested in.
      while (
        next_percentile_index < len(percentiles)
        and current_percentage > percentiles[next_percentile_index]
      ):
        if not 'high' in bucket:
          result[percentiles[next_percentile_index]] = bucket['low']
        else:
          result[percentiles[next_percentile_index]] = float(
            _BucketInterpolate(
              old_percentage,
              percentiles[next_percentile_index],
              current_percentage,
              bucket['low'],
              bucket['high'],
            )
          )
        next_percentile_index += 1
  return result


def MakeWorldReadable(path):
  """Change the permissions of the given path to make it world-readable.
  This is often needed for archived files, so they can be served by web servers
  or accessed by unprivileged network users."""

  # No need to do anything special on Windows.
  if IsWindows():
    return

  perms = stat.S_IMODE(os.stat(path)[stat.ST_MODE])
  if os.path.isdir(path):
    # Directories need read and exec.
    os.chmod(path, perms | 0o555)
  else:
    os.chmod(path, perms | 0o444)


def MakeParentDirectoriesWorldReadable(path):
  """Changes the permissions of the given path and its parent directories
  to make them world-readable. Stops on first directory which is
  world-readable. This is often needed for archive staging directories,
  so that they can be served by web servers or accessed by unprivileged
  network users."""

  # No need to do anything special on Windows.
  if IsWindows():
    return

  while path != os.path.dirname(path):
    current_permissions = stat.S_IMODE(os.stat(path)[stat.ST_MODE])
    if current_permissions & 0o555 == 0o555:
      break
    os.chmod(path, current_permissions | 0o555)
    path = os.path.dirname(path)


def MaybeMakeDirectory(*path):
  """Creates an entire path, if it doesn't already exist."""
  file_path = os.path.join(*path)
  try:
    os.makedirs(file_path)
  except OSError as e:
    if e.errno != errno.EEXIST:
      raise


def RemoveFile(*path):
  """Removes the file located at 'path', if it exists."""
  file_path = os.path.join(*path)
  try:
    os.remove(file_path)
  except OSError as e:
    if e.errno != errno.ENOENT:
      raise


def MoveFile(path, new_path):
  """Moves the file located at 'path' to 'new_path', if it exists."""
  try:
    RemoveFile(new_path)
    os.rename(path, new_path)
  except OSError as e:
    if e.errno != errno.ENOENT:
      raise


def LocateFiles(pattern, root=os.curdir):
  """Yeilds files matching pattern found in root and its subdirectories.

  An exception is thrown if root doesn't exist."""
  for path, _, files in os.walk(os.path.abspath(root)):
    for filename in fnmatch.filter(files, pattern):
      yield os.path.join(path, filename)


def RemoveDirectory(*path):
  """Recursively removes a directory, even if it's marked read-only.

  Remove the directory located at *path, if it exists.

  shutil.rmtree() doesn't work on Windows if any of the files or directories
  are read-only, which svn repositories and some .svn files are.  We need to
  be able to force the files to be writable (i.e., deletable) as we traverse
  the tree.

  Even with all this, Windows still sometimes fails to delete a file, citing
  a permission error (maybe something to do with antivirus scans or disk
  indexing).  The best suggestion any of the user forums had was to wait a
  bit and try again, so we do that too.  It's hand-waving, but sometimes it
  works. :/
  """
  file_path = os.path.join(*path)
  if not os.path.exists(file_path):
    return

  if sys.platform == 'win32':
    # Give up and use cmd.exe's rd command.
    file_path = os.path.normcase(file_path)
    cmd = f'cmd.exe /c rd /q /s "{file_path}"'
    for _ in range(3):
      print(f'RemoveDirectory running {cmd}')
      if not subprocess.call(cmd):
        break
      print('  Failed')
      time.sleep(3)
    return

  def RemoveWithRetry_non_win(rmfunc, path):
    if os.path.islink(path):
      return os.remove(path)
    return rmfunc(path)

  remove_with_retry = RemoveWithRetry_non_win

  def RmTreeOnError(function, path, excinfo):
    r"""This works around a problem whereby python 2.x on Windows has no ability
    to check for symbolic links.  os.path.islink always returns False.  But
    shutil.rmtree will fail if invoked on a symbolic link whose target was
    deleted before the link.  E.g., reproduce like this:
    > mkdir test
    > mkdir test\1
    > mklink /D test\current test\1
    > python -c "import chromium_utils; chromium_utils.RemoveDirectory('test')"
    To avoid this issue, we pass this error-handling function to rmtree.  If
    we see the exact sort of failure, we ignore it.  All other failures we re-
    raise.
    """

    exception_type = excinfo[0]
    exception_value = excinfo[1]
    # If shutil.rmtree encounters a symbolic link on Windows, os.listdir will
    # fail with a WindowsError exception with an ENOENT errno (i.e., file not
    # found).  We'll ignore that error.  Note that WindowsError is not defined
    # for non-Windows platforms, so we use OSError (of which it is a subclass)
    # to avoid lint complaints about an undefined global on non-Windows
    # platforms.
    if (function is os.listdir) and issubclass(exception_type, OSError):
      if exception_value.errno == errno.ENOENT:
        # File does not exist, and we're trying to delete, so we can ignore the
        # failure.
        print('WARNING:  Failed to list %s during rmtree.  Ignoring.\n' % path)
      else:
        raise
    else:
      raise

  for root, dirs, files in os.walk(file_path, topdown=False):
    # For POSIX:  making the directory writable guarantees removability.
    # Windows will ignore the non-read-only bits in the chmod value.
    os.chmod(root, 0o770)
    for name in files:
      remove_with_retry(os.remove, os.path.join(root, name))
    for name in dirs:
      remove_with_retry(
        lambda p: shutil.rmtree(p, onerror=RmTreeOnError),
        os.path.join(root, name),
      )

  remove_with_retry(os.rmdir, file_path)


def _CopyFileToDir(src_path, dest_dir, dest_fn=None, link_ok=False):
  """Copies the file found at src_path to the dest_dir directory, with metadata.

  If dest_fn is specified, the src_path is copied to that name in dest_dir,
  otherwise it is copied to a file of the same name.

  Raises PathNotFound if either the file or the directory is not found.
  """
  # Verify the file and directory separately so we can tell them apart and
  # raise PathNotFound rather than shutil.copyfile's IOError.
  if not os.path.isfile(src_path):
    raise PathNotFound('Unable to find file %s' % src_path)
  if not os.path.isdir(dest_dir):
    raise PathNotFound('Unable to find dir %s' % dest_dir)
  src_file = os.path.basename(src_path)
  if dest_fn:
    # If we have ctypes and the caller doesn't mind links, use that to
    # try to make the copy faster on Windows. http://crbug.com/418702.
    if link_ok and _WIN_LINK_FUNC:
      _WIN_LINK_FUNC(src_path, os.path.join(dest_dir, dest_fn))
    else:
      shutil.copy2(src_path, os.path.join(dest_dir, dest_fn))
  else:
    shutil.copy2(src_path, os.path.join(dest_dir, src_file))


def _GetZipCommand(
  lzma_sdk_bin, archive_name, path=None, file_list=None, no_copy_mode=False
):
  """Generates the zip command depending on the current platform.

  Args:
    lzma_sdk_bin: Optional path to the bin directory of the lzma SDK which
      contains the 7z executable.
    archive_name: The name of the archive to be created.
    path: The relative path to the files to be archived.
    file_list: The list of files/directories to be archived.
    no_copy_mode: Whether we should copy the files to be archived. This mode
      will generate a temporary file.

  Returns:
    A tuple with a list representing the command to be run and a potential
    temporary file used in the no_copy_mode.

    If no command line tool is found, returns None.
  """

  def _FileListToTempFile(file_list, prefix_path=None, glob_dir_content=False):
    tmpfile = tempfile.NamedTemporaryFile(mode='w+t')
    for file in file_list:
      path = file
      if prefix_path is not None:
        path = os.path.join(prefix_path, path)
      if glob_dir_content and not os.path.islink(file) and os.path.isdir(file):
        path = os.path.join(path, '*')
      if os.path.islink(file):
        path = path.rstrip('/')
      tmpfile.write(str(path) + '\n')
    tmpfile.flush()
    return tmpfile

  # If we have 7z, use that as it's much faster. See http://crbug.com/418702.
  # Some bots have 7zip; others don't, so we use the version in the Chromium
  # source tree - see https://crbug.com/1459770
  possible_7zip_locations = []
  if lzma_sdk_bin is not None:
    possible_7zip_locations.append(
      os.path.join(lzma_sdk_bin, '7za.exe' if IsWindows() else '7za')
    )
  if IsWindows():
    possible_7zip_locations.append('C:\\Program Files\\7-Zip\\7z.exe')
  for possible_7zip_location in possible_7zip_locations:
    if os.path.exists(possible_7zip_location):
      cmd = [
        possible_7zip_location,
        'a',  # Add files to archive
        '-tzip',  # Set type of archive to ZIP
        '-y',  # Assume "Yes" to all queries (overwrite without prompt)
        '-mx1',  # Set compression level to 1 (fastest)
        '-uz0',  # Do not update an archive if all files are already up-to-date
        '-bt',  # Show execution time statistics
        '-bb0',  # Set output log level to 0 (no information printed to console)
        '-mmt=on',
      ]
      if not IsWindows():
        cmd += [
          '-snl',  # Store symbolic link as link (to mirror zip -y behaviour)
        ]

      tmpfile = None
      if no_copy_mode:
        assert path is None or path == '.'
        tmpfile = _FileListToTempFile(
          file_list=file_list,
          prefix_path=None if path == "." else path,
          glob_dir_content=False,
        )
        cmd += ['-spf', f'-i@{tmpfile.name}', archive_name]
      else:
        cmd += [archive_name, path]

      return (cmd, tmpfile)

  if IsWindows():
    return (None, None)

  cmd = [
    'zip',
    '-yr1',
    archive_name,
  ]
  tmpfile = None

  if no_copy_mode:
    assert path is None or path == '.'
    tmpfile = _FileListToTempFile(
      file_list,
      prefix_path=None if path == "." else path,
      glob_dir_content=True,
    )
    cmd += ['.', f'-i@{tmpfile.name}']
  else:
    cmd += [path]
  return (cmd, tmpfile)


def MakeZip(
  output_dir,
  archive_name,
  file_list,
  file_relative_dir,
  raise_error=True,
  remove_archive_directory=True,
  no_root_dir=False,
  strip_files=None,
  lzma_sdk_bin=None,
):
  """Packs files into a new zip archive.

  Files are first copied into a directory within the output_dir named for
  the archive_name, which will be created if necessary and emptied if it
  already exists.  The files are then then packed using archive names
  relative to the output_dir.  That is, if the zipfile is unpacked in place,
  it will create a directory identical to the new archive_name directory, in
  the output_dir.  The zip file will be named as the archive_name, plus
  '.zip'.

  Args:
    output_dir: Absolute path to the directory in which the archive is to
      be created.
    archive_name: Subdirectory of output_dir holding files to be added to
      the new zipfile.
    file_list: List of paths to files or subdirectories, relative to the
      file_relative_dir.
    file_relative_dir: Absolute path to the directory containing the files
      and subdirectories in the file_list.
    raise_error: Whether to raise a PathNotFound error if one of the files in
      the list is not found.
    remove_archive_directory: Whether to remove the archive staging directory
      before copying files over to it.
    no_root_dir: Do not add a common root directory for the whole archive.
    strip_files: List of executable files to strip symbols when zipping. The
      option currently does not work in Windows.
    lzma_sdk_bin: Optional path to the bin directory of the lzma SDK which
      contains the 7z executable.

  Returns:
    A tuple consisting of (archive_dir, zip_file_path), where archive_dir
    is the full path to the newly created archive_name subdirectory.

  Raises:
    PathNotFound if any of the files in the list is not found, unless
    raise_error is False, in which case the error will be ignored.
  """
  if not strip_files:
    strip_files = []
  start_time = time.time()
  # Collect files into the archive directory.
  archive_dir = os.path.join(output_dir, archive_name)
  print('output_dir: %s, archive_name: %s' % (output_dir, archive_name))
  print(
    'archive_dir: %s, remove_archive_directory: %s, exists: %s'
    % (archive_dir, remove_archive_directory, os.path.exists(archive_dir))
  )
  if remove_archive_directory and os.path.exists(archive_dir):
    # Move it even if it's not a directory as expected. This can happen with
    # FILES.cfg archive creation where we create an archive staging directory
    # that is the same name as the ultimate archive name.
    if not os.path.isdir(archive_dir):
      print('Moving old "%s" file to create same name directory.' % archive_dir)
      previous_archive_file = '%s.old' % archive_dir
      MoveFile(archive_dir, previous_archive_file)
    else:
      print('Removing %s' % archive_dir)
      RemoveDirectory(archive_dir)
      print(
        'Now, os.path.exists(%s): %s'
        % (archive_dir, os.path.exists(archive_dir))
      )

  # Unfortunately, due to special handling of symbolic and hard links,
  # no_copy_mode cannot be enabled on windows.
  no_copy_mode = no_root_dir and (len(strip_files) == 0) and not IsWindows()

  MaybeMakeDirectory(archive_dir)
  if not no_copy_mode:
    for needed_file in file_list:
      needed_file = needed_file.rstrip()
      print('Copying: %s' % needed_file)
      # These paths are relative to the file_relative_dir.  We need to copy
      # them over maintaining the relative directories, where applicable.
      src_path = os.path.join(file_relative_dir, needed_file)
      dst_path = os.path.join(archive_dir, needed_file)
      dirname, basename = os.path.split(needed_file)
      dest_dir = os.path.join(archive_dir, dirname)
      if dest_dir != archive_dir:
        MaybeMakeDirectory(dest_dir)
      try:
        if os.path.islink(src_path):
          # Need to re-create symlink at dst_path to preserve build structure.
          # Otherwise, shutil.copytree copies whole dir (crbug.com/693624#c35)
          # or shutil.copy2 copies file contents (crbug.com/825553#c13).
          os.symlink(os.readlink(src_path), dst_path)
        else:
          if os.path.isdir(src_path):
            if _WIN_LINK_FUNC:
              _WIN_LINK_FUNC(src_path, dst_path)
            else:
              shutil.copytree(src_path, dst_path, symlinks=True)
          else:
            _CopyFileToDir(src_path, dest_dir, basename, link_ok=True)
            if not IsWindows() and basename in strip_files:
              cmd = ['strip', dst_path]
              RunCommand(cmd)
      except PathNotFound:
        if raise_error:
          raise
  end_time = time.time()
  print(
    'Took %f seconds to create archive directory.' % (end_time - start_time)
  )

  # Pack the zip file.
  output_file = '%s.zip' % archive_dir
  previous_file = '%s_old.zip' % archive_dir
  MoveFile(output_file, previous_file)

  zip_cmd = None
  tmpfile = None
  archive_path = "." if no_root_dir else os.path.basename(archive_dir)
  (zip_cmd, tmpfile) = _GetZipCommand(
    lzma_sdk_bin=lzma_sdk_bin,
    archive_name=output_file,
    path=archive_path,
    no_copy_mode=no_copy_mode,
    file_list=file_list,
  )

  # On Windows we use the python zip module; on Linux and Mac, we use the zip
  # command as it will handle links and file bits (executable).  Which is much
  # easier then trying to do that with ZipInfo options.
  start_time = time.time()
  if IsWindows() and not zip_cmd:
    print('Creating %s' % output_file)

    def _Addfiles(to_zip_file, dirname, files_to_add):
      for this_file in files_to_add:
        archive_name = this_file
        this_path = os.path.join(dirname, this_file)
        if os.path.isfile(this_path):
          # Store files named relative to the outer output_dir.
          archive_name = this_path.replace(output_dir + os.sep, '')
          if os.path.getsize(this_path) == 0:
            compress_method = zipfile.ZIP_STORED
          else:
            compress_method = zipfile.ZIP_DEFLATED
          to_zip_file.write(this_path, archive_name, compress_method)
          print('Adding %s' % archive_name)

    zip_file = zipfile.ZipFile(
      output_file, 'w', zipfile.ZIP_DEFLATED, allowZip64=True
    )
    saved_dir = None
    try:
      if no_root_dir:
        saved_dir = os.getcwd()
        os.chdir(archive_dir)
        walk_path = '.'
      else:
        walk_path = archive_dir
      for root, _, files in os.walk(walk_path):
        _Addfiles(zip_file, root, files)
    finally:
      zip_file.close()
      if saved_dir:
        os.chdir(saved_dir)
  else:
    assert zip_cmd is not None
    saved_dir = os.getcwd()
    if no_copy_mode:
      os.chdir(file_relative_dir)
    elif no_root_dir:
      os.chdir(archive_dir)
    else:
      os.chdir(os.path.dirname(archive_dir))
    result = RunCommand(zip_cmd)
    os.chdir(saved_dir)
    if tmpfile is not None:
      tmpfile.close()
    if result and raise_error:
      raise ExternalError('zip failed: %s => %s' % (str(zip_cmd), result))
  end_time = time.time()
  print('Took %f seconds to create zip.' % (end_time - start_time))
  return (archive_dir, output_file)


def ExtractZip(filename, output_dir, verbose=True):
  """Extract the zip archive in the output directory."""
  MaybeMakeDirectory(output_dir)

  # On Linux and Mac, we use the unzip command as it will
  # handle links and file bits (executable), which is much
  # easier then trying to do that with ZipInfo options.
  #
  # On Windows, try to use 7z if it is installed, otherwise fall back to python
  # zip module and pray we don't have files larger than 512MB to unzip.
  unzip_cmd = None
  if IsLinux():
    unzip_cmd = ['unzip', '-o']
  elif IsMac():
    # The Mac version of unzip does not have LARGE_FILE_SUPPORT until
    # macOS 10.12, so use ditto instead. The Python ZipFile fallback
    # used on Windows does not support symbolic links, which makes it
    # unsuitable for Mac builds.
    unzip_cmd = ['ditto', '-x', '-k']
  elif IsWindows() and os.path.exists('C:\\Program Files\\7-Zip\\7z.exe'):
    unzip_cmd = ['C:\\Program Files\\7-Zip\\7z.exe', 'x', '-y']

  if unzip_cmd:
    # Make sure path is absolute before changing directories.
    filepath = os.path.abspath(filename)
    saved_dir = os.getcwd()
    os.chdir(output_dir)
    command = unzip_cmd + [filepath]
    # When using ditto, a destination is required.
    if command[0] == 'ditto':
      command += ['.']
    result = RunCommand(command)
    os.chdir(saved_dir)
    if result:
      raise ExternalError('unzip failed: %s => %s' % (str(command), result))
  else:
    assert IsWindows()
    zf = zipfile.ZipFile(filename)
    # TODO(hinoka): This can be multiprocessed.
    for name in zf.namelist():
      if verbose:
        print('Extracting %s' % name)
      zf.extract(name, output_dir)
      if IsMac():
        # Restore permission bits.
        os.chmod(
          os.path.join(output_dir, name), zf.getinfo(name).external_attr >> 16
        )


def _FindUpwardParent(start_dir, *desired_list):
  """Finds the desired object's parent, searching upward from the start_dir.

  Searches within start_dir and within all its parents looking for the desired
  directory or file, which may be given in one or more path components. Returns
  the first directory in which the top desired path component was found, or
  raises PathNotFound if it wasn't.
  """
  desired_path = os.path.join(*desired_list)
  last_dir = ''
  cur_dir = start_dir
  found_path = os.path.join(cur_dir, desired_path)
  while not os.path.exists(found_path):
    last_dir = cur_dir
    cur_dir = os.path.dirname(cur_dir)
    if last_dir == cur_dir:
      raise PathNotFound(
        'Unable to find %s above %s' % (desired_path, start_dir)
      )
    found_path = os.path.join(cur_dir, desired_path)
  # Strip the entire original desired path from the end of the one found
  # and remove a trailing path separator, if present (unless it's
  # filesystem/drive root).
  found_path = found_path[: len(found_path) - len(desired_path)]
  if found_path.endswith(os.sep) and os.path.dirname(found_path) != found_path:
    found_path = found_path[: len(found_path) - 1]
  return found_path


def FindUpward(start_dir, *desired_list):
  """Returns a path to the desired directory or file, searching upward.

  Searches within start_dir and within all its parents looking for the desired
  directory or file, which may be given in one or more path components. Returns
  the full path to the desired object, or raises PathNotFound if it wasn't
  found.
  """
  parent = _FindUpwardParent(start_dir, *desired_list)
  return os.path.join(parent, *desired_list)


def RunAndPrintDots(function):
  """Starts a background thread that prints dots while the function runs."""

  def Hook(*args, **kwargs):
    event = threading.Event()

    def PrintDots():
      counter = 0
      while not event.isSet():
        event.wait(5)
        sys.stdout.write('.')
        counter = (counter + 1) % 80
        if not counter:
          sys.stdout.write('\n')
        sys.stdout.flush()

    t = threading.Thread(target=PrintDots)
    t.start()
    try:
      return function(*args, **kwargs)
    finally:
      event.set()
      t.join()

  return Hook


class RunCommandFilter:
  """Class that should be subclassed to provide a filter for RunCommand."""

  # Method could be a function
  # pylint: disable=R0201

  def FilterLine(self, line):
    """Called for each line of input.  The \n is included on a_line.  Should
    return what is to be recorded as the output for this line.  A result of
    None suppresses the line."""
    return line

  def FilterDone(self, remaining_text):
    """Acts just like FilterLine, but is called with any data collected after
    the last newline of the command."""
    return remaining_text


class FilterCapture(RunCommandFilter):
  """Captures the text and places it into an array."""

  def __init__(self):
    RunCommandFilter.__init__(self)
    self.text = []

  def FilterLine(self, line):
    self.text.append(line.rstrip())

  def FilterDone(self, remaining_text):
    self.text.append(remaining_text)


def RunCommand(
  command,
  parser_func=None,
  filter_obj=None,
  pipes=None,
  print_cmd=True,
  timeout=None,
  max_time=None,
  **kwargs,
):
  """Runs the command list, printing its output and returning its exit status.

  Prints the given command (which should be a list of one or more strings),
  then runs it and writes its stdout and stderr to the appropriate file handles.

  If timeout is set, the process will be killed if output is stopped after
  timeout seconds. If max_time is set, the process will be killed if it runs for
  more than max_time.

  If parser_func is not given, the subprocess's output is passed to stdout
  and stderr directly.  If the func is given, each line of the subprocess's
  stdout/stderr is passed to the func and then written to stdout.

  If filter_obj is given, all output is run through the filter a line
  at a time before it is written to stdout.

  We do not currently support parsing stdout and stderr independent of
  each other.  In previous attempts, this led to output ordering issues.
  By merging them when either needs to be parsed, we avoid those ordering
  issues completely.

  pipes is a list of commands (also a list) that will receive the output of
  the intial command. For example, if you want to run "python a | python b | c",
  the "command" will be set to ['python', 'a'], while pipes will be set to
  [['python', 'b'],['c']]
  """

  def TimedFlush(timeout, fh, kill_event):
    """Flush fh every timeout seconds until kill_event is true."""
    while True:
      try:
        fh.flush()
      # File handle is closed, exit.
      except ValueError:
        break
      # Wait for kill signal or timeout.
      if kill_event.wait(timeout):
        break
    print(threading.currentThread(), 'TimedFlush: Finished')

  def ProcessRead(
    proc,
    writefh,
    parser_func=None,
    filter_obj=None,
    log_event=None,
    debug=False,
  ):
    writefh.flush()

    # Python on Windows writes the buffer only when it reaches 4k.  Ideally
    # we would flush a minimum of 10 seconds.  However, we only write and
    # flush no more often than 20 seconds to avoid flooding the master with
    # network traffic from unbuffered output.
    kill_event = threading.Event()
    flush_thread = threading.Thread(
      target=TimedFlush, args=(20, writefh, kill_event)
    )
    flush_thread.daemon = True
    flush_thread.start()

    try:
      in_byte = proc.stdout.read(1)
      in_line = io.StringIO()
      while in_byte:
        # Capture all characters except \r.
        if in_byte != b'\r':
          in_line.write(in_byte.decode())

        # Write and flush on newline.
        if in_byte == b'\n':
          if log_event:
            log_event.set()
          if parser_func:
            parser_func(in_line.getvalue().strip())

          if filter_obj:
            filtered_line = filter_obj.FilterLine(in_line.getvalue())
            if filtered_line is not None:
              writefh.write(filtered_line)
          else:
            writefh.write(in_line.getvalue())
          in_line = io.StringIO()
        if debug and proc.poll() is not None:
          print('Child process has terminated')
        in_byte = proc.stdout.read(1)

      print(threading.currentThread(), 'ProcessRead: proc.stdout finished.')

      if log_event and in_line.getvalue():
        log_event.set()

      # Write remaining data and flush on EOF.
      if parser_func:
        parser_func(in_line.getvalue().strip())

      if filter_obj:
        if in_line.getvalue():
          filtered_line = filter_obj.FilterDone(in_line.getvalue())
          if filtered_line is not None:
            writefh.write(filtered_line)
      else:
        if in_line.getvalue():
          writefh.write(in_line.getvalue())
    finally:
      print(threading.currentThread(), 'ProcessRead: cleaning up.')

      # Consume outputs from proc to prevent deadlock in wait due to full of
      # buffer in process pipes.
      # See note in https://docs.python.org/3/library/subprocess.html#subprocess.Popen.wait
      proc_out, proc_err = proc.communicate()
      if proc_out:
        print('stdout from proc: %s', proc_out)
      if proc_err:
        print('stderr from proc: %s', proc_err)

      kill_event.set()
      flush_thread.join()
      writefh.flush()
      print(threading.currentThread(), 'ProcessRead: finished.')

  pipes = pipes or []

  # Print the given command (which should be a list of one or more strings).
  if print_cmd:
    print('\n' + subprocess.list2cmdline(command) + '\n', end=' ')
    for pipe in pipes:
      print('     | ' + subprocess.list2cmdline(pipe) + '\n', end=' ')

  sys.stdout.flush()
  sys.stderr.flush()

  if not (parser_func or filter_obj or pipes or timeout or max_time):
    # Run the command.  The stdout and stderr file handles are passed to the
    # subprocess directly for writing.  No processing happens on the output of
    # the subprocess.
    proc = subprocess.Popen(
      command, stdout=sys.stdout, stderr=sys.stderr, bufsize=0, **kwargs
    )

    # Wait for the command to terminate.
    proc.wait()
    assert proc.returncode is not None
    return proc.returncode

  if not (parser_func or filter_obj):
    filter_obj = RunCommandFilter()

  # Start the initial process.
  proc = subprocess.Popen(
    command,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    bufsize=0,
    **kwargs,
  )
  proc_handles = [proc]

  if pipes:
    pipe_number = 0
    for pipe in pipes:
      pipe_number = pipe_number + 1
      if pipe_number == len(pipes) and not (parser_func or filter_obj):
        # The last pipe process needs to output to sys.stdout or filter
        stdout = sys.stdout
      else:
        # Output to a pipe, since another pipe is on top of us.
        stdout = subprocess.PIPE
      pipe_proc = subprocess.Popen(
        pipe,
        stdin=proc_handles[0].stdout,
        stdout=stdout,
        stderr=subprocess.STDOUT,
      )
      proc_handles.insert(0, pipe_proc)

    # Allow proc to receive a SIGPIPE if the piped process exits.
    for handle in proc_handles[1:]:
      handle.stdout.close()

  log_event = threading.Event()

  # Launch and start the reader thread.
  thread = threading.Thread(
    target=ProcessRead,
    args=(proc_handles[0], sys.stdout),
    kwargs={
      'parser_func': parser_func,
      'filter_obj': filter_obj,
      'log_event': log_event,
    },
  )

  kill_lock = threading.Lock()

  def term_then_kill(handle, initial_timeout, numtimeouts, interval):

    def timed_check():
      for _ in range(numtimeouts):
        if handle.poll() is not None:
          return True
        time.sleep(interval)

    handle.terminate()
    time.sleep(initial_timeout)
    timed_check()
    if handle.poll() is None:
      handle.kill()
    timed_check()
    return handle.poll() is not None

  def kill_proc(proc_handles, message=None):
    with kill_lock:
      if proc_handles:
        killed = term_then_kill(proc_handles[0], 0.1, 5, 1)

        if message:
          print(message, file=sys.stderr)

        if not killed:
          print('could not kill pid %d!' % proc_handles[0].pid, file=sys.stderr)
        else:
          print(
            'program finished with exit code %d' % (proc_handles[0].returncode),
            file=sys.stderr,
          )

        # Prevent other timeouts from double-killing.
        del proc_handles[:]

  def timeout_func(timeout, proc_handles, log_event, finished_event):
    while log_event.wait(timeout):
      log_event.clear()
      if finished_event.is_set():
        return

    message = (
      'command timed out: %d seconds without output, attempting to '
      'kill' % timeout
    )
    kill_proc(proc_handles, message)

  def maxtimeout_func(timeout, proc_handles, finished_event):
    if not finished_event.wait(timeout):
      message = 'command timed out: %d seconds elapsed' % timeout
      kill_proc(proc_handles, message)

  timeout_thread = None
  maxtimeout_thread = None
  finished_event = threading.Event()

  if timeout:
    timeout_thread = threading.Thread(
      target=timeout_func,
      args=(timeout, proc_handles, log_event, finished_event),
    )
    timeout_thread.daemon = True
  if max_time:
    maxtimeout_thread = threading.Thread(
      target=maxtimeout_func, args=(max_time, proc_handles, finished_event)
    )
    maxtimeout_thread.daemon = True

  thread.start()
  if timeout_thread:
    timeout_thread.start()
  if maxtimeout_thread:
    maxtimeout_thread.start()

  # Wait for the commands to terminate.
  for handle in proc_handles:
    handle.wait()
    assert handle.returncode is not None

  # Wake up timeout threads.
  finished_event.set()
  log_event.set()

  thread.join()

  # Check whether any of the sub commands has failed.
  for handle in proc_handles:
    assert handle.returncode is not None
    if handle.returncode:
      return handle.returncode

  assert proc.returncode is not None
  return proc.returncode


def GetStatusOutput(command, **kwargs):
  """Runs the command list, returning its result and output."""
  proc = subprocess.Popen(
    command,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    bufsize=1,
    text=True,
    **kwargs,
  )
  output = proc.communicate()[0]
  result = proc.returncode

  return (result, output)


def GetCommandOutput(command):
  """Runs the command list, returning its output.

  Run the command and returns its output (stdout and stderr) as a string.

  If the command exits with an error, raises ExternalError.
  """
  (result, output) = GetStatusOutput(command)
  if result:
    raise ExternalError('%s: %s' % (subprocess.list2cmdline(command), output))
  return output


def _convert_json(option, _, value, parser):
  """Provide an OptionParser callback to unmarshal a JSON string."""
  setattr(parser.values, option.dest, json.loads(value))


def AddPropertiesOptions(option_parser):
  """Registers command line options for parsing build properties.

  After parsing, the options object will have the 'build_properties'
  attribute. The corresponding values will be python dictionaries
  containing the properties. If the options are not given on the command
  line, the dictionaries will be empty.

  Args:
    option_parser: An optparse.OptionParser to register command line options
                   for build properties.
  """
  option_parser.add_option(
    '--build-properties',
    action='callback',
    callback=_convert_json,
    type='string',
    nargs=1,
    default={},
    help='build properties in JSON format',
  )


def FileExclusions():
  all_platforms = [
    '.landmines',
    '.ninja_deps',
    '.ninja_log',
    'gen',
    '*/gen',
    'obj',
    '*/obj',
    'thinlto-cache',
    '*/thinlto-cache',
  ]
  # Skip files that the testers don't care about. Mostly directories.
  if IsWindows():
    # Remove obj or lib dir entries
    return all_platforms + ['cfinstaller_archive', 'lib', 'installer_archive']
  if IsMac():
    return all_platforms + [
      # We don't need the arm bits v8 builds.
      'd8_arm',
      'v8_shell_arm',
      # pdfsqueeze is a build helper, no need to copy it to testers.
      'pdfsqueeze',
      # We copy the framework into the app bundle, we don't need the second
      # copy outside the app.
      # TODO(mark): Since r28431, the copy in the build directory is actually
      # used by tests.  Putting two copies in the .zip isn't great, so maybe
      # we can find another workaround.
      # 'Chromium Framework.framework',
      # 'Google Chrome Framework.framework',
      # We copy the Helper into the app bundle, we don't need the second
      # copy outside the app.
      'Chromium Helper.app',
      'Google Chrome Helper.app',
      'App Shim Socket',
      '.deps',
      'obj.host',
      'obj.target',
      'lib',
    ]
  if IsLinux():
    return all_platforms + [
      # intermediate build directories (full of .o, .d, etc.).
      'appcache',
      'glue',
      'lib.host',
      'obj.host',
      'obj.target',
      'src',
      '.deps',
      # scons build cruft
      '.sconsign.dblite',
      # build helper, not needed on testers
      'mksnapshot',
    ]

  return all_platforms
