# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import unittest
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open, call

import git_data_processor

# Helper constants for 40-char hashes
HASH_1 = "1111111111111111111111111111111111111111"
HASH_2 = "2222222222222222222222222222222222222222"
HASH_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
HASH_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
HASH_C = "cccccccccccccccccccccccccccccccccccccccc"
HASH_OLD = "oooooooooooooooooooooooooooooooooooooooo"
HASH_NEW = "nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn"


class TestBlameParsing(unittest.TestCase):
  """Tests for parsing git blame --porcelain output."""

  @patch('subprocess.run')
  def test_get_blame_data_success(self, mock_run):
    mock_file = MagicMock(spec=Path)
    mock_file.is_file.return_value = True
    mock_file.parent = Path(".")
    mock_file.name = "test.py"

    mock_output = (
      f"{HASH_A} 1 1 1\n"
      "author User One\n"
      "\tline_of_code = 1\n"
      f"{HASH_A} 1 2 1\n"
      "\tline_of_code = 2\n"
      f"{HASH_B} 2 3 1\n"
      "author User Two\n"
      "\tline_of_code = 3\n"
    )
    mock_run.return_value.stdout = mock_output

    result = git_data_processor.get_blame_data(mock_file)

    # We expect a list of hashes corresponding to the lines
    expected = [HASH_A, HASH_A, HASH_B]
    self.assertEqual(result, expected)
    self.assertTrue(all(len(h) == 40 for h in result))


class TestProcessFileLogic(unittest.TestCase):
  """Tests the file processing logic."""

  @patch('pathlib.Path.mkdir')
  @patch('git_data_processor.get_blame_data')
  @patch('git_data_processor.get_file_hash')
  @patch('builtins.open', new_callable=mock_open)
  def test_process_file_success(
    self, mock_open_file, mock_hash, mock_blame, mock_mkdir
  ):
    # Setup: Standard success case
    mock_hash.return_value = HASH_1
    mock_blame.return_value = [HASH_A, HASH_B]

    input_dir = Path("/root")
    output_dir = Path("/out")
    file_path = Path("/root/dir/file.py")

    git_data_processor.process_file(file_path, input_dir, output_dir)

    # Expect write
    mock_open_file.assert_called()

    # Verify content written contains version and data
    # mock_open_file() returns the file handle mock when called (as open() would)
    handle = mock_open_file()
    written_data = "".join(c[0][0] for c in handle.write.call_args_list)
    parsed = json.loads(written_data)

    self.assertEqual(parsed['file_hash'], HASH_1)
    self.assertEqual(parsed['lines'], [HASH_A, HASH_B])
    self.assertEqual(parsed['version'], git_data_processor.VERSION)


class TestCommitMetadata(unittest.TestCase):
  """Tests for parsing commit messages and extracting metadata tags."""

  @patch('subprocess.run')
  def test_get_commit_details_parsing(self, mock_run):
    # Simulate `git show` output with Chromium/Google style tags
    raw_output = (
      "Subject line of the commit\n"
      "\n"
      "Body of the commit message.\n"
      "\n"
      "Bug: 123, 456\n"
      "Change-Id: Iabc123\n"
      "Reviewed-by: User <u@example.com>\n"
      "Test=Unit tests passed\n"
      "\n"
      "--author--\n"
      "Dev Name <dev@example.com>\n"
      "--date--\n"
      "2025-01-01 10:00:00"
    )
    mock_run.return_value.stdout = raw_output

    # Pass a 40-char commit hash
    details = git_data_processor.get_commit_details(HASH_A)

    self.assertEqual(details['author'], "Dev Name <dev@example.com>")
    self.assertEqual(details['date'], "2025-01-01 10:00:00")

    # Verify metadata extraction
    meta = details['metadata']
    self.assertEqual(meta['Bug'], ['123', '456'])
    self.assertEqual(meta['Change-Id'], 'Iabc123')
    self.assertEqual(meta['TEST='], 'Unit tests passed')

    # Verify message cleanup (tags should be stripped from main message)
    self.assertIn("Subject line", details['message'])
    self.assertNotIn("Bug: 123", details['message'])

  @patch('subprocess.run')
  def test_get_commit_details_unicode_replace(self, mock_run):
    # Simulate non-UTF8 characters replaced with replacement character
    raw_output = (
      "Subject with non-utf8 char: \ufffd\n"
      "\n"
      "--author--\n"
      "Dev Name \ufffd <dev@example.com>\n"
      "--date--\n"
      "2025-01-01 10:00:00"
    )
    mock_run.return_value.stdout = raw_output

    details = git_data_processor.get_commit_details(HASH_A)
    self.assertIsNotNone(details)
    self.assertEqual(details['author'], "Dev Name \ufffd <dev@example.com>")
    self.assertIn("non-utf8 char", details['message'])
    self.assertEqual(mock_run.call_args.kwargs.get('errors'), 'replace')

  @patch('subprocess.run')
  def test_get_commit_details_exception_handling(self, mock_run):
    mock_run.side_effect = subprocess.SubprocessError("Command failed")
    details = git_data_processor.get_commit_details(HASH_A)
    self.assertIsNone(details)


class TestCommandFlows(unittest.TestCase):
  """Tests for the high-level CLI command functions."""

  @patch('pathlib.Path.mkdir')
  @patch('git_data_processor.process_file')
  @patch('subprocess.run')
  def test_blame_command_exclude_logic(
    self, mock_run, mock_process_file, mock_mkdir
  ):
    # Mock arguments
    args = MagicMock()
    # Use absolute path for source_dir to simplify mock resolution
    args.source_dir = "/repo"
    args.directory = "src"
    args.output_dir = "/out"
    args.workers = 1
    args.baseline_commit = None

    # The script calculates excludes relative to git root.
    # If git_root is /repo, and we process /repo/src/third_party/lib.py,
    # the relative path is src/third_party/lib.py.
    args.exclude_directories = "src/third_party, src/tests"

    # Mock git rev-parse (git root)
    mock_run.side_effect = [
      MagicMock(stdout="/repo\n"),
      # Mock git ls-files returning 3 files relative to the input dir (src)
      MagicMock(stdout="main.py\nthird_party/lib.py\ntests/test.py\n"),
    ]

    # Define a side effect for Path.resolve that returns CONCRETE Path objects.
    # This ensures strict=True passes and arithmetic like '/' works.
    def fake_resolve(self, strict=False):
      path_str = str(self)
      # If resolving the "src" directory (input_dir calculation)
      if path_str.endswith("src"):
        return Path("/repo/src")
      # If resolving the source_dir (git_root calculation)
      return Path("/repo")

    # Patch Path.resolve with autospec=True so 'self' is passed correctly
    with patch('pathlib.Path.resolve', autospec=True, side_effect=fake_resolve):
      with patch('pathlib.Path.is_dir', return_value=True):
        with patch('pathlib.Path.is_file', return_value=True):
          git_data_processor.blame(args)

    # Verification:
    # 1. main.py -> /repo/src/main.py -> rel /repo -> "src/main.py". Match exclude? No. KEPT.
    # 2. third_party/lib.py -> /repo/src/third_party/lib.py -> rel /repo -> "src/third_party/lib.py". Match "src/third_party"? Yes. EXCLUDED.
    # 3. tests/test.py -> /repo/src/tests/test.py -> rel /repo -> "src/tests/test.py". Match "src/tests"? Yes. EXCLUDED.

    self.assertEqual(mock_process_file.call_count, 1)

    # Ensure the correct file was processed
    processed_arg = mock_process_file.call_args[0][0]
    self.assertTrue(str(processed_arg).endswith("main.py"))

  @patch('pathlib.Path.mkdir')
  @patch('git_data_processor.collect_blame_details')
  @patch('json.dump')
  @patch('builtins.open', new_callable=mock_open)
  def test_collect_command(
    self, mock_open_file, mock_dump, mock_collect, mock_mkdir
  ):
    args = MagicMock()
    args.directory = "blame_indices"
    args.output_file = "final.json"
    args.source_dir = "."
    args.context = 5
    args.workers = 4

    mock_collect.return_value = {HASH_A: {"data": "val"}}

    # Simpler resolve mock for this test
    with patch('pathlib.Path.resolve', return_value=Path("/abs/path")):
      with patch('pathlib.Path.is_dir', return_value=True):
        git_data_processor.collect(args)

    mock_collect.assert_called_once()
    mock_dump.assert_called()

  @patch('pathlib.Path.glob')
  @patch('pathlib.Path.mkdir')
  @patch('git_data_processor.process_commit_hash')
  @patch('json.load')
  @patch('builtins.open', new_callable=mock_open)
  def test_fetch_command(
    self, mock_open_file, mock_load, mock_process_hash, mock_mkdir, mock_glob
  ):
    args = MagicMock()
    args.blame_details_file = "input.json"
    args.output_dir = "out_dir"
    args.source_dir = "."
    args.workers = 2

    mock_glob.return_value = []
    mock_load.return_value = {HASH_A: {}, HASH_B: {}}

    with patch('pathlib.Path.resolve', return_value=Path("/abs/path")):
      git_data_processor.fetch(args)

    self.assertEqual(mock_process_hash.call_count, 2)


if __name__ == '__main__':
  unittest.main()
