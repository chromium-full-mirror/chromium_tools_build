# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
A unified script for processing git blame data, collecting commit hashes,
and fetching commit details.
"""

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import threading
from functools import wraps
from pathlib import Path
from collections import defaultdict
from typing import List, Tuple, Optional

# The version of the JSON output format.
# Increment this when changing the data processing logic.
VERSION = "0.1"


def get_git_root(git_dir: Optional[str] = None) -> str:
  """
  Gets the root directory of the git repository.

  Args:
      git_dir: Path to the git repository.

  Returns:
      The absolute path to the git repository root.
  """
  try:
    cmd = ['git']
    if git_dir:
      cmd.extend(['-C', git_dir])
    cmd.extend(['rev-parse', '--show-toplevel'])
    result = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      check=True,
      encoding='utf-8',
      errors='replace',
    )
    return result.stdout.strip()
  except (subprocess.CalledProcessError, FileNotFoundError) as e:
    print(f"Error finding git root: {e}", file=sys.stderr)
    sys.exit(1)


def get_file_hash(file_path: Path) -> Optional[str]:
  """
  Gets the git blob hash of a file's current content on disk.

  Args:
      file_path: The path to the file.

  Returns:
      The git blob hash as a string, or None if an error occurs.
  """
  if not file_path.is_file():
    return None
  try:
    cmd = ['git', '-C', str(file_path.parent), 'hash-object', file_path.name]
    result = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      check=True,
      encoding='utf-8',
      errors='replace',
    )
    return result.stdout.strip()
  except (subprocess.CalledProcessError, FileNotFoundError) as e:
    print(f"Error getting hash for {file_path}: {e}", file=sys.stderr)
    return None


def get_blame_data(file_path: Path) -> Optional[List[str]]:
  """
  Runs `git blame --porcelain` and parses the output to get the commit
  hash for each line.

  Args:
      file_path: The path to the file.

  Returns:
      A list of commit hashes, one for each line in the file, or None
      if an error occurs.
  """
  if not file_path.is_file():
    return None
  try:
    cmd = [
      'git',
      '-C',
      str(file_path.parent),
      'blame',
      '--porcelain',
      file_path.name,
    ]
    result = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      check=True,
      encoding='utf-8',
      errors='replace',
    )

    lines = result.stdout.splitlines()
    blame_commits = []
    current_commit = None

    # A regex to identify a line containing a commit hash.
    commit_re = re.compile(r'^[0-9a-f]{40}')

    for line in lines:
      if commit_re.match(line):
        current_commit = line.split(' ')[0]
      elif line.startswith('\t'):
        # This is a content line; its blame info is the last commit we saw.
        blame_commits.append(current_commit)

    return blame_commits
  except (subprocess.CalledProcessError, FileNotFoundError) as e:
    # Git blame can fail on binary files, which is expected.
    if (
      'fatal: no such path' not in e.stderr
      and 'fatal: Not a valid object name' not in e.stderr
    ):
      print(
        f"Could not get blame for {file_path}: {e.stderr.strip()}",
        file=sys.stderr,
      )
    return None


def process_file(file_path: Path, input_dir: Path, output_dir: Path):
  """
  Processes a single file: checks if its index is up-to-date and
  recomputes it if necessary. Saves an empty blame file on error.

  Args:
      file_path: Absolute path to the file to process.
      input_dir: Absolute path to the root directory being scanned.
      output_dir: Absolute path to the directory for storing indices.
  """
  relative_path = file_path.relative_to(input_dir)
  index_path = (output_dir / relative_path).with_suffix(
    file_path.suffix + '.json'
  )

  try:
    current_hash = get_file_hash(file_path)
    if not current_hash:
      # Error is printed within get_file_hash
      raise ValueError("Failed to get file hash")
    blame_data = get_blame_data(file_path)
    if blame_data is None:
      # Error is printed within get_blame_data for relevant cases
      raise ValueError("Failed to get blame data")

    index_data = {
      'version': VERSION,
      'file_hash': current_hash,
      'lines': blame_data,
    }

    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, 'w', encoding='utf-8') as f:
      json.dump(index_data, f)

  except Exception as e:
    print(
      f"Error processing {relative_path}: {e}. Saving empty blame.",
      file=sys.stderr,
    )

    # In case of error, we still want to record the hash if possible.
    current_hash_for_error = get_file_hash(file_path)
    new_index_data = {
      'version': VERSION,
      'file_hash': current_hash_for_error
      if current_hash_for_error
      else "error",
      'lines': [],
    }
    try:
      index_path.parent.mkdir(parents=True, exist_ok=True)
      with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(new_index_data, f)
    except IOError as e_write:
      print(
        f"Error writing empty index file {index_path}: {e_write}",
        file=sys.stderr,
      )


def blame(args):
  """The 'blame' sub-command."""
  git_root = Path(args.source_dir).resolve(
    strict=True
  )  # Path(get_git_root(args.source_dir))
  print("git_root", git_root)
  print("get_git_root", get_git_root(args.source_dir))

  try:
    # The input directory is relative to the git root.
    input_dir = (git_root / args.directory).resolve(strict=True)
    # The output path is the output directory combined with the input
    # directory.
    output_dir = (Path(args.output_dir) / args.directory).resolve()
  except FileNotFoundError:
    print(
      f"Error: Input directory '{args.directory}' not found relative to git root '{git_root}'.",
      file=sys.stderr,
    )
    sys.exit(1)

  if not input_dir.is_dir():
    print(f"Error: '{input_dir}' is not a directory.", file=sys.stderr)
    sys.exit(1)

  output_dir.mkdir(parents=True, exist_ok=True)

  try:
    print("input_dir", input_dir)
    directory_relative_to_git_root = input_dir.relative_to(git_root)

    if args.baseline_commit:
      print(
        f"Finding changed files since baseline commit: {args.baseline_commit} in '{directory_relative_to_git_root}'"
      )
      # Use `git diff` to find files changed since the baseline.
      # We run from input_dir and check against HEAD for files in the
      # current directory ('.'). This ensures output paths are
      # relative to input_dir.
      cmd = [
        'git',
        '-C',
        str(input_dir),
        'diff',
        '--name-only',
        args.baseline_commit,
        'HEAD',
        '--',
        '.',
      ]
    else:
      print(f"Finding all tracked files in '{directory_relative_to_git_root}'.")
      # Use `git ls-files` to get all tracked files in the directory.
      # Running from input_dir with '.' gives us relative paths.
      cmd = ['git', '-C', str(input_dir), 'ls-files', '.']

    result = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      check=True,
      encoding='utf-8',
      errors='replace',
    )
    files_to_process = result.stdout.splitlines()

  except (subprocess.CalledProcessError, FileNotFoundError) as e:
    # Check if the error is from a subprocess and has the specific stderr message
    if (
      args.baseline_commit
      and isinstance(e, subprocess.CalledProcessError)
      and ('unknown revision' in e.stderr or 'bad revision' in e.stderr)
    ):
      print(
        f"Error: Invalid baseline commit '{args.baseline_commit}'.",
        file=sys.stderr,
      )
    # Handle FileNotFoundError (e.g., 'git' command not found)
    elif isinstance(e, FileNotFoundError):
      print(
        "Error: 'git' command not found. Please ensure git is installed and in your PATH.",
        file=sys.stderr,
      )
    # Handle other generic errors
    else:
      print(f"Error listing files with git: {e}", file=sys.stderr)
      if isinstance(e, subprocess.CalledProcessError):
        print(f"Subprocess stderr: {e.stderr}", file=sys.stderr)
      print(
        "Please ensure you are running this script inside a git repository.",
        file=sys.stderr,
      )
    sys.exit(1)

  if args.exclude_directories:
    excluded_dirs = [
      d.strip() for d in args.exclude_directories.split(',') if d.strip()
    ]

    filtered_files = []
    for file_str in files_to_process:
      file_path = str((input_dir / file_str).relative_to(git_root))
      is_excluded = any(
        file_path.startswith(excluded_dir + os.sep) or file_path == excluded_dir
        for excluded_dir in excluded_dirs
      )
      if not is_excluded:
        filtered_files.append(file_str)

    print(
      f"Excluded {len(files_to_process) - len(filtered_files)} files based on exclude list."
    )
    files_to_process = filtered_files

  print(
    f"Found {len(files_to_process)} files to process in '{directory_relative_to_git_root}'."
  )
  print(f"Using {args.workers} worker threads.")

  file_paths = [
    input_dir / file_rel_path
    for file_rel_path in files_to_process
    if (input_dir / file_rel_path).is_file()
  ]

  with concurrent.futures.ThreadPoolExecutor(
    max_workers=args.workers
  ) as executor:
    # Use a map to submit jobs to the thread pool.
    # We use a lambda to pass the constant input_dir and output_dir arguments.
    # The list() wrapper is used to ensure all tasks are completed.
    # Exceptions are handled within process_file.
    print(f"Processing {len(file_paths)} files...")
    list(
      executor.map(lambda p: process_file(p, input_dir, output_dir), file_paths)
    )


# Cache for git root lookups to avoid repeatedly running git commands.
_git_root_cache = {}


def get_git_root_for_file(file_path: Path) -> Optional[str]:
  """
  Finds the git repository root for a given file path.
  Caches results based on the file's parent directory to improve performance.

  Args:
      file_path: The absolute path to the file.

  Returns:
      The absolute path to the git repository root as a string, or None if
      not found.
  """
  dir_path = file_path.parent
  if dir_path in _git_root_cache:
    return _git_root_cache[dir_path]

  if not dir_path.is_dir():
    _git_root_cache[dir_path] = None
    return None

  try:
    cmd = ['git', '-C', str(dir_path), 'rev-parse', '--show-toplevel']
    result = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      check=True,
      encoding='utf-8',
      errors='replace',
    )
    root = result.stdout.strip()
    _git_root_cache[dir_path] = root
    return root
  except (subprocess.CalledProcessError, FileNotFoundError):
    # This directory is not in a git repository, or git is not installed.
    _git_root_cache[dir_path] = None
    return None


def _process_blame_file(file_info: Tuple[Path, Path]) -> defaultdict:
  """Helper to process a single blame file for collect_blame_details."""
  json_file, blame_dir = file_info
  commit_file_lines = defaultdict(lambda: defaultdict(set))
  relative_path_str = str(json_file.relative_to(blame_dir))

  # This check is a bit redundant if the glob is specific, but safe.
  if not relative_path_str.endswith('.json'):
    return commit_file_lines

  source_relative_path = Path(relative_path_str[:-5])

  try:
    with open(json_file, 'r', encoding='utf-8', errors='replace') as f:
      blame_data = json.load(f)

    if 'lines' in blame_data and isinstance(blame_data['lines'], list):
      for i, commit_hash in enumerate(blame_data['lines']):
        if commit_hash:
          commit_file_lines[commit_hash][str(source_relative_path)].add(i)
  except (json.JSONDecodeError, IOError) as e:
    print(f"Error reading or parsing {json_file}: {e}", file=sys.stderr)
  return commit_file_lines


def _determine_repo_for_commit(
  commit_info: Tuple[str, dict, Path, Optional[Path]],
) -> Tuple[str, str]:
  """Helper to find the repository for a given commit."""
  commit_hash, files, source_dir, git_root = commit_info
  any_file_path_str = next(iter(files.keys()))
  source_file_path = source_dir / any_file_path_str
  repo_path_str = get_git_root_for_file(source_file_path)

  repo = "unknown"
  if repo_path_str and git_root:
    try:
      repo_path = Path(repo_path_str)
      relative_repo_path = repo_path.relative_to(git_root)
      repo = str(relative_repo_path)
    except ValueError:
      repo = repo_path_str
  elif repo_path_str:
    repo = repo_path_str

  return commit_hash, repo


def thread_safe_memoize(func):
  cache = {}
  lock = threading.Lock()

  @wraps(func)
  def memoized_func(*args, **kwargs):
    key = (
      args,
      frozenset(kwargs.items()),
    )  # Create a hashable key for args and kwargs

    with lock:  # Acquire lock before accessing the cache
      if key in cache:
        return cache[key]

    # If not in cache, compute the result outside the lock to avoid blocking
    result = func(*args, **kwargs)

    with lock:  # Acquire lock again to store the result
      cache[key] = result
      return result

  return memoized_func


@thread_safe_memoize
def read_source_file(file_path: Path) -> Optional[List[str]]:
  try:
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
      source_lines = f.readlines()
      return source_lines
  except IOError as e:
    print(f"Error reading source file {file_path}: {e}", file=sys.stderr)
    return None


def _aggregate_context_for_commit(
  commit_info: Tuple[str, dict, Path, int, dict],
) -> Optional[Tuple[str, dict]]:
  """Helper to aggregate blame context for a single commit."""
  commit_hash, files, source_dir, context, commit_to_repo = commit_info
  commit_files_data = {}
  for file_path_str, line_indices in files.items():
    source_file_path = source_dir / file_path_str
    if not source_file_path.is_file():
      continue

    try:
      source_lines = read_source_file(source_file_path)
      if not source_lines:
        continue
      lines_with_context = set()
      for i in line_indices:
        start = max(0, i - context)
        end = min(len(source_lines), i + context + 1)
        for j in range(start, end):
          lines_with_context.add(j)

      file_entry = []
      for i in sorted(list(lines_with_context)):
        line_info = [i + 1, source_lines[i].rstrip('\n')]
        file_entry.append(line_info)

      if file_entry:
        commit_files_data[file_path_str] = file_entry
    except IOError as e:
      print(
        f"Error reading source file {source_file_path}: {e}", file=sys.stderr
      )

  if commit_files_data:
    return commit_hash, {
      "repository": commit_to_repo.get(commit_hash, "unknown"),
      "files": commit_files_data,
    }
  return None


def collect_blame_details(
  blame_dir: Path,
  source_dir: Path,
  context: int,
  git_root: Optional[Path],
  workers: int,
) -> dict:
  """
  Scans a directory for .json blame files and aggregates blame details.

  Args:
      blame_dir: The directory containing the .json blame index files.
      source_dir: The root directory of the source code.
      context: The number of lines of context to include around each blamed line.
      git_root: The root directory of the main git repository. If provided,
                repository paths will be relative to this root.
      workers: The number of worker threads to use.

  Returns:
      A dictionary mapping commit hashes to their repository and blamed files.
  """
  # Step 1: Collect all blamed line numbers for each commit and file.
  commit_file_lines = defaultdict(lambda: defaultdict(set))
  json_files = list(blame_dir.rglob('*.json'))
  print(f"Found {len(json_files)} JSON files to process in '{blame_dir}'.")

  with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
    file_infos = [(f, blame_dir) for f in json_files]
    print(f"Scanning {len(json_files)} blame files...")
    results = list(executor.map(_process_blame_file, file_infos))

  for result in results:
    for commit_hash, files in result.items():
      for file_path, lines in files.items():
        commit_file_lines[commit_hash][file_path].update(lines)

  # Step 2: Determine repository for each commit hash.
  commit_to_repo = {}
  with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
    commit_infos = [
      (commit, files, source_dir, git_root)
      for commit, files in commit_file_lines.items()
    ]
    print(f"Determining repositories for {len(commit_infos)} commits...")
    results = list(executor.map(_determine_repo_for_commit, commit_infos))

  for commit_hash, repo in results:
    commit_to_repo[commit_hash] = repo

  # Step 3: Process collected data to expand context and fetch line content.
  final_output = {}
  with concurrent.futures.ThreadPoolExecutor(
    max_workers=min(10, workers)
  ) as executor:
    commit_infos = [
      (commit, files, source_dir, context, commit_to_repo)
      for commit, files in commit_file_lines.items()
    ]
    print(f"Aggregating context for {len(commit_infos)} commits...")
    results = list(executor.map(_aggregate_context_for_commit, commit_infos))

  for result in filter(None, results):
    commit_hash, data = result
    final_output[commit_hash] = data

  return final_output


def collect(args):
  """The 'collect' sub-command."""
  try:
    blame_dir = Path(args.directory).resolve(strict=True)
    output_file = Path(args.output_file).resolve()
    git_root = (
      Path(args.source_dir).resolve(strict=True) if args.source_dir else None
    )

    if git_root:
      # If git_root is provided, source_dir is relative to it.
      source_dir = (git_root / args.source_dir).resolve(strict=True)
    else:
      # Otherwise, it's relative to the current working directory.
      source_dir = Path(args.source_dir).resolve(strict=True)

  except FileNotFoundError as e:
    print(f"Error: A specified directory was not found: {e}", file=sys.stderr)
    sys.exit(1)

  if not blame_dir.is_dir() or not source_dir.is_dir():
    print("Error: Provided paths must be directories.", file=sys.stderr)
    sys.exit(1)

  output_file.parent.mkdir(parents=True, exist_ok=True)

  blame_details = collect_blame_details(
    blame_dir, source_dir, args.context, git_root, args.workers
  )

  print(f"Found blame data for {len(blame_details)} unique commit hashes.")

  try:
    with open(output_file, 'w', encoding='utf-8') as f:
      json.dump(blame_details, f, indent=2)
    print(f"Successfully saved aggregated blame data to {output_file}")
  except IOError as e:
    print(f"Error writing to output file {output_file}: {e}", file=sys.stderr)
    sys.exit(1)


def get_commit_details(
  commit_hash: str, git_dir: Optional[str] = None
) -> Optional[dict]:
  """
  Retrieves the message, author, and date for a given commit hash.
  Also extracts metadata from the commit message.

  Args:
      commit_hash: The git commit hash.
      git_dir: Path to the git repository.

  Returns:
      A dictionary with commit details, or None if an error occurs.
  """
  try:
    cmd = ['git']
    if git_dir:
      cmd.extend(['-C', git_dir])
    cmd.extend(
      ['show', '-s', '--format=%B%n--author--%n%an%n--date--%n%ai', commit_hash]
    )
    result = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      check=True,
      encoding='utf-8',
      errors='replace',
    )

    output = result.stdout.strip()
    parts = output.split('\n--author--\n')
    message_raw = parts[0]

    author_parts = parts[1].split('\n--date--\n')
    author = author_parts[0]
    date = author_parts[1]

    metadata_prefixes = {
      "Bug": "Bug:",
      "Change-Id": "Change-Id:",
      "Reviewed-on": "Reviewed-on:",
      "Reviewed-by": "Reviewed-by:",
      "Commit-Queue": "Commit-Queue:",
      "Cr-Commit-Position": "Cr-Commit-Position:",
      "Review-Url": "Review-Url:",
      "Auto-Submit:": "Auto-Submit:",
      "Code-Coverage:": "Code-Coverage:",
      "Owners-Override:": "Owners-Override:",
      "Cq-Include-Trybots:": "Cq-Include-Trybots:",
      "CQ_INCLUDE_TRYBOTS=": "CQ_INCLUDE_TRYBOTS=",
      "R=": "R=",
      "TBR=": "TBR=",
      "BUG=": "BUG=",
      "TEST=": "TEST=",
      "NOTRY=": "NOTRY=",
      "Review URL:": "Review URL:",
      "git-svn-id:": "git-svn-id:",
      "Bot-Commit:": "Bot-Commit:",
      "No-Presubmit:": "No-Presubmit:",
      "No-Tree-Checks:": "No-Tree-Checks",
      "Fixed:": "Fixed:",
    }

    message_lines = message_raw.strip().split('\n')
    cleaned_message_lines = []
    metadata = {}

    for line in message_lines:
      # Skip lines that are quotes
      if line.startswith('>'):
        continue
      found_prefix = False
      for key, prefix in metadata_prefixes.items():
        if line.strip().lower().startswith(prefix.lower()):
          value = line.strip()[len(prefix) :].strip()
          # Standardize how bug IDs are stored.
          if key in ["Bug", "BUG="]:
            bugs_raw = value.split(',')
            bug_numbers = []
            for bug_str in bugs_raw:
              # Remove all non-digit characters
              # bug_num_str = re.sub(r'\D', '', bug_str)
              # if bug_num_str:
              bug_numbers.append(bug_str.strip())
            metadata['Bug'] = bug_numbers
          else:
            metadata[key] = value
          found_prefix = True
          break
      if not found_prefix:
        cleaned_message_lines.append(line)

    message = '\n'.join(cleaned_message_lines).strip()

    return {
      'message': message,
      'author': author.strip(),
      'date': date.strip(),
      'metadata': metadata,
    }
  except Exception as e:
    print(
      f"Error getting details for commit {commit_hash}: {e}", file=sys.stderr
    )
    return None


def process_commit_hash(
  commit_hash: str,
  commit_data: dict,
  output_dir: Path,
  git_root: Optional[Path] = None,
):
  """
  Processes a single commit hash: fetches details, combines with line data,
  and saves them to a file.

  Args:
      commit_hash: The commit hash to process.
      commit_data: A dictionary containing repository and file blame data.
      output_dir: The directory to save the output JSON file.
      git_root: The root directory of the main git repository.
  """
  output_file = output_dir / f"{commit_hash}.json"

  if output_file.exists():
    try:
      with open(output_file, 'r', encoding='utf-8', errors='replace') as f:
        existing_data = json.load(f)
      if existing_data.get('version') == VERSION:
        return
    except (json.JSONDecodeError, IOError) as e:
      print(
        f"Error reading existing detail file {output_file}. Re-fetching. Error: {e}",
        file=sys.stderr,
      )

  repo_path = commit_data.get("repository", ".")
  if git_root and repo_path != "unknown":
    git_dir = str(git_root / repo_path)
  else:
    git_dir = str(git_root) if git_root else None

  commit_details = get_commit_details(commit_hash, git_dir)

  if commit_details is None:
    # Error is printed within get_commit_details
    return

  # Add version and propagate the line data into the final output
  commit_details['version'] = VERSION
  commit_details['repository'] = repo_path
  commit_details['files'] = commit_data.get("files", {})

  try:
    with open(output_file, 'w', encoding='utf-8') as f:
      json.dump(commit_details, f, indent=2)
  except IOError as e:
    print(f"Error writing file {output_file}: {e}", file=sys.stderr)


def fetch(args):
  """The 'fetch' sub-command."""
  try:
    blame_details_file = Path(args.blame_details_file).resolve(strict=True)
    output_dir = Path(args.output_dir).resolve()
    git_root = (
      Path(args.source_dir).resolve(strict=True) if args.source_dir else None
    )
  except FileNotFoundError as e:
    print(f"Error: A specified directory was not found: {e}", file=sys.stderr)
    sys.exit(1)

  output_dir.mkdir(parents=True, exist_ok=True)

  try:
    with open(blame_details_file, 'r', encoding='utf-8', errors='replace') as f:
      blame_details = json.load(f)
    if not isinstance(blame_details, dict):
      raise TypeError("Input file should contain a JSON dictionary.")
  except (json.JSONDecodeError, TypeError, IOError) as e:
    print(
      f"Error reading or parsing blame details file {blame_details_file}: {e}",
      file=sys.stderr,
    )
    sys.exit(1)

  print(f"Found {len(blame_details)} commit hashes to process.")
  print(f"Using {args.workers} worker threads.")

  with concurrent.futures.ThreadPoolExecutor(
    max_workers=args.workers
  ) as executor:
    # Create a list of items to process for the map function
    items_to_process = list(blame_details.items())
    # Use a lambda to pass arguments correctly to the mapped function
    print(f"Fetching details for {len(items_to_process)} commits...")
    list(
      executor.map(
        lambda item: process_commit_hash(
          commit_hash=item[0],
          commit_data=item[1],
          output_dir=output_dir,
          git_root=git_root,
        ),
        items_to_process,
      )
    )


def main():
  """Parses command-line arguments and starts the indexing process."""
  parser = argparse.ArgumentParser(
    description="A unified script for processing git blame data."
  )
  subparsers = parser.add_subparsers(dest="command", required=True)

  # Blame command
  blame_parser = subparsers.add_parser(
    "blame", help="Compute and cache git blame data for files in a directory."
  )
  blame_parser.add_argument(
    "directory",
    type=str,
    help="The directory to process files from, relative to the source dir (e.g., '.' or 'src/').",
  )
  blame_parser.add_argument(
    "--output-dir",
    type=str,
    required=True,
    help="The directory to save the blame index files.",
  )
  blame_parser.add_argument(
    "--source-dir",
    type=str,
    default=".",
    help="Path to the git repository directory (e.g., '/path/to/repo').",
  )
  blame_parser.add_argument(
    "-w",
    "--workers",
    type=int,
    default=os.cpu_count(),
    help="Number of worker threads to use. Defaults to the number of CPU cores.",
  )
  blame_parser.add_argument(
    "--exclude-directories",
    type=str,
    default="",
    help="Comma-separated list of directories to exclude, relative to the input directory.",
  )
  blame_parser.add_argument(
    "--baseline-commit",
    type=str,
    default=None,
    help="If provided, only process files that have changed since this commit.",
  )
  blame_parser.set_defaults(func=blame)

  # Collect command
  collect_parser = subparsers.add_parser(
    "collect",
    help="Collect unique commit hashes and their blamed lines from index files.",
  )
  collect_parser.add_argument(
    "directory",
    type=str,
    help="The directory containing the blame index .json files.",
  )
  collect_parser.add_argument(
    "--source-dir",
    type=str,
    default=".",
    help="The root directory of the source code repository. Defaults to the current directory.",
  )
  collect_parser.add_argument(
    "--output-file",
    type=str,
    required=True,
    help="The path to the output file to save the aggregated blame data.",
  )
  collect_parser.add_argument(
    "--context",
    type=int,
    default=5,
    help="Number of lines of context to include before and after each blamed line. Default is 5.",
  )
  collect_parser.add_argument(
    "-w",
    "--workers",
    type=int,
    default=os.cpu_count(),
    help="Number of worker threads to use. Defaults to the number of CPU cores.",
  )
  collect_parser.set_defaults(func=collect)

  # Fetch command
  fetch_parser = subparsers.add_parser(
    "fetch", help="Fetch git commit details and combine with blamed line data."
  )
  fetch_parser.add_argument(
    "blame_details_file",
    type=str,
    help="The JSON file from the 'collect' command.",
  )
  fetch_parser.add_argument(
    "--output-dir",
    type=str,
    required=True,
    help="The directory to save the commit detail JSON files.",
  )
  fetch_parser.add_argument(
    "--source-dir",
    type=str,
    default=".",
    help="The root directory of the main git repository.",
  )
  fetch_parser.add_argument(
    "-w",
    "--workers",
    type=int,
    default=os.cpu_count(),
    help="Number of worker threads to use. Defaults to the number of CPU cores.",
  )
  fetch_parser.set_defaults(func=fetch)

  args = parser.parse_args()
  args.func(args)


if __name__ == "__main__":
  main()
