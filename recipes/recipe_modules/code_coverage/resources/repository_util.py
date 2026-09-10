# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""A script to retrieve the last changed revision of files in the checkout.

It parses the DEPS file to look for dependency repositories, and runs "git log"
to get the last changed revision of files.
"""

from __future__ import annotations

import collections
from collections.abc import MutableMapping
from concurrent.futures import ThreadPoolExecutor
import csv
import logging
import multiprocessing
import os
import platform
import subprocess
import tempfile
import time
from typing import Iterable, Iterator, NamedTuple, Tuple

import diff_util

IS_WIN = platform.system() == 'Windows'
GIT = 'git' if not IS_WIN else 'git.bat'


class _VarImpl:
  def __init__(self, local_scope):
    self._local_scope = local_scope

  def Lookup(self, var_name):
    if var_name not in self._local_scope.get('vars', {}):
      raise KeyError('Var is not defined: %s' % var_name)
    return self._local_scope['vars'][var_name]


class _Timer:
  def __init__(self):
    self._time = None

  def Start(self):
    self._time = time.time()

  def End(self, msg):
    new_time = time.time()
    elapsed_time = new_time - self._time
    logging.info('%s took %.0f seconds', msg, elapsed_time)


def _GetOrderedCheckoutDirOfDependenciesFromDEPS(deps_content):
  """Returns the paths to the checkouts of all dependencies in the given DEPS.

  By default, '//' is added as the root checkout of the given DEPS.

  Args:
    deps_content (str): the content of a DEPS file. It is assumed to be trusted
      and will be evaluated as python code.

  Returns:
    A list of file paths in descending order of the length of file paths.
    A path starts with '//' as the root, and ends with '/'.
  """
  local_scope = {
    'vars': {},
    'allowed_hosts': [],
    'deps': {},
    'deps_os': {},
    'include_rules': [],
    'skip_child_includes': [],
    'hooks': [],
    'Str': str,
  }
  var = _VarImpl(local_scope)
  global_scope = {
    'Var': var.Lookup,
    'vars': {},
    'allowed_hosts': [],
    'deps': {},
    'deps_os': {},
    'include_rules': [],
    'skip_child_includes': [],
    'hooks': [],
    'Str': str,
  }
  exec(deps_content, global_scope, local_scope)

  checkout_dirs = list(local_scope['deps'].keys())
  for _, deps_os_checkout_dirs in local_scope['deps_os'].items():
    checkout_dirs.extend(deps_os_checkout_dirs)

  src_checkout_paths = ['//']

  root_dir = 'src/'
  for path in checkout_dirs:
    if path.startswith(root_dir):
      path = path[len(root_dir) :]
    path = '//' + path
    if not path.endswith('/'):
      path += '/'
    src_checkout_paths.append(path)
  # pylint: disable=unnecessary-lambda
  src_checkout_paths.sort(key=lambda x: len(x), reverse=True)
  return src_checkout_paths


class GitLogEntry(NamedTuple):
  commit: str
  timestamp: int
  path: str


def _ParseGitLogStream(
  git_log_output_stream: Iterable[str],
) -> Iterator[GitLogEntry]:
  """Parses git log stream and yields GitLogEntry tuples.

  Expected input format:
    COMMIT <hash> <timestamp>
    file1
    file2
    COMMIT <hash> <timestamp>
    file3
  """
  current_commit = None
  current_timestamp = None
  for line in git_log_output_stream:
    line = line.strip()
    if not line:
      continue
    if line.startswith('COMMIT '):
      parts = line.split(' ')
      if len(parts) >= 3:
        current_commit = parts[1]
        current_timestamp = int(parts[2])
      else:
        logging.warning('Unexpected COMMIT line format: %s', line)
    else:
      if current_commit and current_timestamp:
        yield GitLogEntry(current_commit, current_timestamp, line)
      else:
        logging.warning('File path encountered before any COMMIT: %s', line)


RevisionCacheKey = Tuple[str, str]
LastModifiedInfo = Tuple[str, int]


class RevisionCache:
  """A file-based cache that maps paths in checkouts to last modified info.

  This cache should be transparent to `_GetFileRevisions()` callers. Its
  purpose is to speed up populating coverage reports with revision information
  when `generate_coverage_metadata.py` is invoked more than once in a static
  checkout.

  Additionally, the cache supports batch resolution (via `BatchRetrieve`) to
  drastically reduce the number of spawned `git` processes on the first run
  in a large checkout by fetching history for multiple files at once.

  The serialization format is CSV so that the cache can parse or format
  entries line-by-line.

  The `TryLoad()` and `Flush()` operations are not threadsafe. `TryLoad()` will
  tolerate an absent or corrupt `.csv`.
  """

  def __init__(self, path: os.PathLike):
    self._path = path
    self._entries: MutableMapping[RevisionCacheKey, LastModifiedInfo] = {}
    self._dirty = False

  def TryLoad(self):
    try:
      self._entries.clear()
      self._dirty = False
      with open(self._path) as cache_path:
        reader = csv.reader(cache_path)
        for checkout_dir, path_in_checkout, revision, timestamp in reader:
          self._entries[checkout_dir, path_in_checkout] = (
            revision,
            int(timestamp),
          )
    except FileNotFoundError:
      logging.warning('Revision cache %s not found', self._path)
    except (OSError, csv.Error, ValueError):
      self.Clear()

  def Flush(self):
    try:
      if not self._dirty:
        return
      os.makedirs(os.path.dirname(self._path), exist_ok=True)
      with open(self._path, 'w+') as cache_path:
        writer = csv.writer(cache_path)
        for cache_key, (revision, timestamp) in self._entries.items():
          checkout_dir, path_in_checkout = cache_key
          writer.writerow([checkout_dir, path_in_checkout, revision, timestamp])
      self._dirty = False
    except OSError:
      self.Clear()

  def Clear(self):
    logging.warning(
      'Deleting revision cache %s, which may be corrupt', self._path
    )
    os.remove(self._path)

  def RetrieveRevision(self, args):
    """Returns the path, git hash, and last changed timestamp of the given file.

    Args:
      args (tuple): A tuple <root_dir, checkout_dir, path> where
        * root_dir (str): System absolute path to the root checkout.
        * checkout_dir (str): Source absolute path to the root of a dependency
          checkout.
        * path (str): Source absolute path to the file to retrieve the revision.

    Returns:
      A tuple of three elements:
        1. Source absolute path to the file.
        2. Git hash of the commit when the file was most recently updated.
        3. Time stamp of the commit when the file was most recently updated.
    """
    assert len(args) == 3, 'Got %d args, but expected 3' % (len(args))
    root_dir, checkout_dir, path = args

    assert checkout_dir.startswith('//'), (
      '%s is expected to start with //' % checkout_dir
    )
    cwd = os.path.join(root_dir, checkout_dir[2:])

    assert path.startswith('//'), '%s is expected to start with //' % path
    path_in_dep_repo = path[len(checkout_dir) :]

    cache_key = (checkout_dir, path_in_dep_repo)
    if entry := self._entries.get(cache_key):
      return (path, *entry)

    try:
      git_output = subprocess.check_output(
        [GIT, 'log', '-n', '1', '--pretty=format:%H:%ct', path_in_dep_repo],
        cwd=cwd,
        text=True,
      )

      lines = git_output.splitlines()
      assert len(lines) == 1, 'More than one line output.'

      parts = lines[0].split(':')
      assert len(parts) == 2, 'not in format "git_hash:timestamp"'
    except (subprocess.CalledProcessError, AssertionError):
      print('Failed to retrieve revision for %s: %r' % (checkout_dir, path))
      return None

    revision, timestamp = self._entries[cache_key] = parts[0], int(parts[1])
    self._dirty = True
    return path, revision, timestamp

  def BatchRetrieve(self, root_dir, checkout_dir, paths_in_checkout):
    """Populates the cache by running git log on the checkout directory.

    Args:
      root_dir (str): System absolute path to the root checkout.
      checkout_dir (str): Source absolute path to the root of a dependency
        checkout.
      paths_in_checkout (list): List of paths in checkout to resolve
    """
    assert checkout_dir.startswith('//'), (
      '%s is expected to start with //' % checkout_dir
    )
    cwd = os.path.join(root_dir, checkout_dir[2:])

    paths_in_checkout_set = set(paths_in_checkout)
    resolved_paths = set()

    cmd = [
      GIT,
      'log',
      '--name-only',
      '--no-renames',
      '--pretty=format:COMMIT %H %ct',
      '--since=5 years ago',
    ]
    try:
      process = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, text=True
      )

      for commit, timestamp, path in _ParseGitLogStream(process.stdout):
        if path in paths_in_checkout_set and path not in resolved_paths:
          cache_key = (checkout_dir, path)
          self._entries[cache_key] = (commit, timestamp)
          self._dirty = True
          resolved_paths.add(path)
          if len(resolved_paths) == len(paths_in_checkout_set):
            process.terminate()
            break

      process.wait()
    except Exception as e:
      logging.warning(
        'Error during batched git log for %s: %s', checkout_dir, e
      )


def _GetCommitedFilesForEachCheckout(root_dir, checkouts):
  """Returns source absolute paths to all committed files in each checkout.

  Args:
    root_dir (str): Absolute path to the directory of the root checkout.
    checkouts (list): A list of source absolute paths to checkout directories.

  Returns:
    A dict mapping from a checkout (in source absolute path) to the list of
    committed files (in source absolute path).
  """
  all_files = collections.defaultdict(set)
  for checkout in checkouts:
    assert checkout.startswith('//')
    checkout_dir = os.path.join(root_dir, checkout[2:])
    if not os.path.isdir(checkout_dir):
      continue
    git_output = subprocess.check_output(
      [GIT, 'ls-files'], cwd=checkout_dir, text=True
    )
    for path in git_output.splitlines():
      all_files[checkout].add(os.path.join(checkout, path))
  return all_files


def _GetFileRevisions(root_dir, deps_file_path, file_paths):
  """Returns a dict mapping from the path to its git revision for given files.

  Args:
    root_dir (str): System absolute path to the directory of the root checkout.
    deps_file_path (str): Relative path to the DEPS file in the root checkout.
    file_paths (list): The list of source absolute file paths to retrieve git
      revisions for.

  Returns:
    A dict that maps from file source absolute paths to tuples of two elements:
      1. Git hash of the commit when the file was most recently updated.
      2. Time stamp of the commit when the file was most recently updated.
  """
  timer = _Timer()
  timer.Start()
  with open(os.path.join(root_dir, deps_file_path), 'r') as f:
    deps_file_content = f.read()
  timer.End('Reading deps file')

  timer.Start()
  checkouts = _GetOrderedCheckoutDirOfDependenciesFromDEPS(deps_file_content)
  timer.End('_GetOrderedCheckoutDirOfDependenciesFromDEPS')

  timer.Start()
  all_files = _GetCommitedFilesForEachCheckout(root_dir, checkouts)
  timer.End('_GetCommitedFilesForEachCheckout')

  timer.Start()
  files_by_checkout = collections.defaultdict(list)
  for path in file_paths:
    assert path.startswith('//'), '%s is expected to start with //' % path
    for checkout in checkouts:
      if path.startswith(checkout) and path in all_files.get(checkout, []):
        files_by_checkout[checkout].append(path)
        break
  timer.End('Grouping files by checkout')

  timer.Start()
  root_revision = subprocess.check_output(
    [GIT, 'rev-parse', 'HEAD'], text=True, cwd=root_dir
  ).strip()
  cache_path = os.path.join(
    tempfile.gettempdir(), '.cov-rev-cache', f'{root_revision}.csv'
  )
  cache = RevisionCache(cache_path)
  cache.TryLoad()

  # Phase 1: Batching
  for checkout, paths in files_by_checkout.items():
    uncached_paths = []
    for path in paths:
      path_in_dep_repo = path[len(checkout) :]
      if (checkout, path_in_dep_repo) not in cache._entries:
        uncached_paths.append(path_in_dep_repo)

    # Batching provides a speedup when we deal with more than ~1500 files
    # This only runs in CI builders with coverage enabled
    if len(uncached_paths) > 1500:
      cache.BatchRetrieve(root_dir, checkout, uncached_paths)
  timer.End('Phase 1: Batching')

  timer.Start()
  # Phase 2: Fallback / Individual
  revisions_by_path = {}
  unresolved_file_data = []
  for checkout, paths in files_by_checkout.items():
    for path in paths:
      path_in_dep_repo = path[len(checkout) :]
      cache_key = (checkout, path_in_dep_repo)
      if entry := cache._entries.get(cache_key):
        revisions_by_path[path] = entry
      else:
        unresolved_file_data.append((root_dir, checkout, path))

  if unresolved_file_data:
    # Scale threads linearly with core count (approximately). Note that all
    # threads but the newest CPython implementations compete to acquire the
    # Global Interpreter Lock (GIL) [0] when executing bytecode. However,
    # because:
    #   * A thread releases the GIL when blocked on I/O,
    #   * ... and `RetrieveRevision()` is I/O-bound waiting for `git log`
    #     subprocesses to join,
    #
    # ... a thread pool is still an effective way to parallelize
    # `RetrieveRevision()`.
    #
    # [0]: https://docs.python.org/3/glossary.html#term-global-interpreter-lock
    max_workers = max(5, multiprocessing.cpu_count() - 5)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
      for result in pool.map(cache.RetrieveRevision, unresolved_file_data):
        if not result:
          continue
        path, git_hash, timestamp = result
        revisions_by_path[path] = git_hash, timestamp
  cache.Flush()
  timer.End('Multithreaded RetrieveRevision')
  return revisions_by_path


def AddGitRevisionsToCoverageFilesMetadata(
  files_coverage_data, src_path, deps_file_path
):
  """Add git revisions to a list File in coverage metadata format.

  Coverage metadata format:
  https://chromium.googlesource.com/infra/infra/+/refs/heads/main/appengine/findit/model/proto/code_coverage.proto

  Args:
    files_coverage_data (list): A list of File in coverage metadata format, and
      it is going to be mutated by this function.
    src_path (str): Absolute path to the source root.
    deps_file_path (str): Relative path to the DEPS file that manages
      dependencies.
  """
  logging.info('Retrieving file git metadata...')
  start_time = time.time()

  all_files = [file_record['path'] for file_record in files_coverage_data]
  file_git_metadata = _GetFileRevisions(src_path, deps_file_path, all_files)
  for file_record in files_coverage_data:
    git_metadata = file_git_metadata.get(file_record['path'])
    if not git_metadata:
      logging.warning(
        'Failed to retrieve git metadata for %s', file_record['path']
      )
      continue

    file_record['revision'], file_record['timestamp'] = git_metadata

  minutes = (time.time() - start_time) / 60
  logging.info(
    'Retrieving and filling in git metadata for %d files took %.0f minutes',
    len(all_files),
    minutes,
  )


def GetUnmodifiedLinesSinceCommit(src_path, file_path, reference_commit):
  """Returns a list of lines unmodified since the reference_commit.

  This function does a git diff against the reference_commit and parses its
  output to find the unmodified lines.

  Args:
    src_path (str): Absolute path to the root of the checkout.
    file_path (str): File path relative to the root of the checkout, without
      leading slashes
    reference_commit (str): Hash of the reference commit
  """
  try:
    show_arg = '%s:%s' % (reference_commit, file_path)
    show_cmd = [GIT, 'show', show_arg]
    show_output = subprocess.check_output(show_cmd, cwd=src_path, text=True)
  except subprocess.CalledProcessError:
    logging.info(
      'Unable to fetch file content at reference_commit.'
      '%s may have been added later or moved.' % file_path
    )
    # Return empty because all lines at HEAD are new
    return []
  reference_commit_lines = show_output.splitlines()

  try:
    diff_cmd = [GIT, 'diff', 'HEAD', reference_commit, '--', file_path]
    diff_output = subprocess.check_output(diff_cmd, cwd=src_path, text=True)
  except subprocess.CalledProcessError:
    logging.error('Unable to calculate diff with reference commit')
    raise
  diff_lines = diff_output.splitlines()

  try:
    show_arg = 'HEAD:%s' % (file_path)
    show_cmd = [GIT, 'show', show_arg]
    show_output = subprocess.check_output(show_cmd, cwd=src_path, text=True)
  except subprocess.CalledProcessError:
    logging.warning('Unable to fetch file content at HEAD for %s', file_path)
    raise
  local_lines = show_output.splitlines()

  unchanged_lines = list(
    diff_util.generate_line_number_mapping(
      diff_lines, local_lines, reference_commit_lines
    ).keys()
  )
  return unchanged_lines
