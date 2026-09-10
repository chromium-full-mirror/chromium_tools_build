# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import unittest
import os
import shutil
import tempfile
import stat
from unittest.mock import patch, MagicMock
from merge_blame_jsons import merge_directories


class TestMergeDirectories(unittest.TestCase):
  """Unit tests for the merge_directories function."""

  def setUp(self):
    """Create two temporary directories for use as Dir A (destination) and Dir B (source)."""
    # Create temporary directories
    self.temp_dir_a = tempfile.mkdtemp()
    self.temp_dir_b = tempfile.mkdtemp()

    # Define paths for convenience
    self.dir_a = self.temp_dir_a
    self.dir_b = self.temp_dir_b

    # Ensure clean slate for permissions tests later
    self.default_umask = os.umask(0o000)

  def tearDown(self):
    """Clean up the temporary directories."""
    # Restore umask
    os.umask(self.default_umask)

    # Clean up the temporary directories only if they still exist.
    # This prevents FileNotFoundError if the test case itself removed the directory.
    if os.path.exists(self.dir_a):
      shutil.rmtree(self.dir_a)
    if os.path.exists(self.dir_b):
      shutil.rmtree(self.dir_b)

  def create_file(self, directory, filename, content=""):
    """Helper to create a file with specified content."""
    filepath = os.path.join(directory, filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
      f.write(content)
    return filepath

  # --- Test Case 1: Basic Merge (New Files) ---

  def test_basic_merge_new_files(self):
    """Test copying new files and directories from B to A."""
    self.create_file(self.dir_b, "file1.txt", "Content from B1")
    self.create_file(
      os.path.join(self.dir_b, "sub_b"), "file2.txt", "Content from B2"
    )

    merge_directories(self.dir_a, self.dir_b)

    # Check if files were created in A
    self.assertTrue(os.path.exists(os.path.join(self.dir_a, "file1.txt")))
    self.assertTrue(
      os.path.exists(os.path.join(self.dir_a, "sub_b", "file2.txt"))
    )

    # Check content of copied file
    with open(
      os.path.join(self.dir_a, "file1.txt"), 'r', encoding='utf-8'
    ) as f:
      self.assertEqual(f.read(), "Content from B1")

  # --- Test Case 2: Overwriting Existing Files ---

  def test_overwrite_existing_file(self):
    """Test that files in B overwrite identically named files in A."""
    # File in A (destination)
    self.create_file(self.dir_a, "common_file.txt", "Old content from A")

    # File in B (source)
    self.create_file(self.dir_b, "common_file.txt", "New content from B")

    merge_directories(self.dir_a, self.dir_b)

    # Check content of the file in A (should be B's content)
    filepath = os.path.join(self.dir_a, "common_file.txt")
    with open(filepath, 'r', encoding='utf-8') as f:
      self.assertEqual(f.read(), "New content from B")

  # --- Test Case 3: Preserving Unique Files in A ---

  def test_preservation_of_unique_files_in_a(self):
    """Test that files and directories unique to A remain untouched."""
    # Unique file in A
    self.create_file(self.dir_a, "unique_a.log", "Only in A")
    # Unique subdirectory in A
    self.create_file(
      os.path.join(self.dir_a, "sub_a"), "file_a.txt", "In A sub_a"
    )

    # File in B
    self.create_file(self.dir_b, "file_b.txt", "Only in B")

    merge_directories(self.dir_a, self.dir_b)

    # Check that unique A files/dirs still exist
    self.assertTrue(os.path.exists(os.path.join(self.dir_a, "unique_a.log")))
    self.assertTrue(
      os.path.exists(os.path.join(self.dir_a, "sub_a", "file_a.txt"))
    )

    # Check content of unique A file is unchanged
    with open(
      os.path.join(self.dir_a, "unique_a.log"), 'r', encoding='utf-8'
    ) as f:
      self.assertEqual(f.read(), "Only in A")

  # --- Test Case 4: Directory Structure Creation ---

  def test_create_nested_directories_in_a(self):
    """Test that a nested directory structure in B is created in A."""
    nested_path = os.path.join("level1", "level2", "level3")
    self.create_file(
      os.path.join(self.dir_b, nested_path), "deep_file.json", "Deep content"
    )

    merge_directories(self.dir_a, self.dir_b)

    dest_file = os.path.join(self.dir_a, nested_path, "deep_file.json")
    self.assertTrue(os.path.exists(dest_file))
    with open(dest_file, 'r', encoding='utf-8') as f:
      self.assertEqual(f.read(), "Deep content")

  # --- Test Case 5: Handling Read-Only Files in A (Permissions) ---

  def test_overwrite_read_only_file(self):
    """Test that a read-only file in A is made writable and overwritten."""
    file_name = "read_only_file.txt"
    dest_path = self.create_file(
      self.dir_a, file_name, "Original protected content"
    )
    source_content = "New, overwritten content"
    self.create_file(self.dir_b, file_name, source_content)

    # 1. Make the destination file read-only (remove write permission for the owner)
    original_mode = os.stat(dest_path).st_mode
    os.chmod(dest_path, original_mode & ~stat.S_IWUSR)

    # Check it's read-only (sanity check)
    self.assertFalse(os.stat(dest_path).st_mode & stat.S_IWUSR)

    # 2. Perform the merge
    merge_directories(self.dir_a, self.dir_b)

    # 3. Check if the file was successfully overwritten
    with open(dest_path, 'r', encoding='utf-8') as f:
      self.assertEqual(f.read(), source_content)

    # 4. Cleanup: Restore original permissions (so tearDown can delete it)
    os.chmod(dest_path, original_mode | stat.S_IWUSR)

  # --- Test Case 6: Edge Cases (Non-Existent Directories) ---

  @patch('builtins.print')
  def test_destination_dir_a_does_not_exist(self, mock_print):
    """Test behavior when Dir A (destination) does not exist."""
    # Remove the temporary directory A created in setUp
    shutil.rmtree(self.dir_a)

    merge_directories(self.dir_a, self.dir_b)

    # Check for the expected error message being printed
    mock_print.assert_any_call(
      f"Error: Destination directory A ('{self.dir_a}') does not exist or is not a directory."
    )
    # Ensure no files were created or modified
    self.assertFalse(os.path.exists(self.dir_a))

  @patch('builtins.print')
  def test_source_dir_b_does_not_exist(self, mock_print):
    """Test behavior when Dir B (source) does not exist."""
    # Remove the temporary directory B created in setUp
    shutil.rmtree(self.dir_b)

    # Create a dummy file in A to ensure A exists before the call
    self.create_file(self.dir_a, "only_a.txt")

    merge_directories(self.dir_a, self.dir_b)

    # Check for the expected error message being printed
    mock_print.assert_any_call(
      f"Error: Source directory B ('{self.dir_b}') does not exist or is not a directory."
    )
    # Ensure A's contents are unchanged
    self.assertTrue(os.path.exists(os.path.join(self.dir_a, "only_a.txt")))

  # --- Test Case 7: Empty Source Directory ---

  def test_empty_source_directory(self):
    """Test behavior when Dir B is empty."""
    self.create_file(self.dir_a, "existing_file.txt", "Content A")
    # Dir B is empty by default

    merge_directories(self.dir_a, self.dir_b)

    # A should remain unchanged, with no new files added
    self.assertTrue(
      os.path.exists(os.path.join(self.dir_a, "existing_file.txt"))
    )
    self.assertEqual(len(os.listdir(self.dir_a)), 1)


if __name__ == '__main__':
  unittest.main()
