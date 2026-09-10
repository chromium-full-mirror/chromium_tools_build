# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import shutil
import argparse
import stat


def merge_directories(dir_a: str, dir_b: str):
  """
  Recursively merges the contents of dir_b (source) into dir_a (destination).

  Files in dir_b will overwrite files with the same relative path in dir_a.
  Ensures destination files are writable before overwriting.
  Directories in dir_a that do not exist in dir_b will be kept.
  Directories in dir_b will be created in dir_a if they don't exist.
  """
  if not os.path.isdir(dir_a):
    print(
      f"Error: Destination directory A ('{dir_a}') does not exist or is not a directory."
    )
    return
  if not os.path.isdir(dir_b):
    print(
      f"Error: Source directory B ('{dir_b}') does not exist or is not a directory."
    )
    return

  print(
    f"Starting merge: Source '{dir_b}' will be merged into destination '{dir_a}'."
  )

  # Walk through the source directory (dir_b)
  for root, _, files in os.walk(dir_b):
    # Construct the corresponding path in the destination (dir_a)
    # The relative path is the part of 'root' after 'dir_b'
    relative_path = os.path.relpath(root, dir_b)
    dest_dir = os.path.join(dir_a, relative_path)

    # 1. Ensure the directory structure exists in the destination
    if not os.path.exists(dest_dir):
      try:
        os.makedirs(dest_dir)
        print(f"Created directory: {dest_dir}")
      except Exception as e:
        print(f"Error creating directory {dest_dir}: {e}")
        continue  # Skip processing files in this directory if creation failed

    # 2. Copy files from source to destination
    for file_name in files:
      source_file = os.path.join(root, file_name)
      dest_file = os.path.join(dest_dir, file_name)

      try:
        # If the destination file exists, make sure it's writable
        if os.path.exists(dest_file):
          try:
            current_mode = os.stat(dest_file).st_mode
            if not (current_mode & stat.S_IWUSR):
              print(f"Making {dest_file} writable")
              os.chmod(dest_file, current_mode | stat.S_IWUSR)
          except Exception as e:
            print(f"Error changing permissions for {dest_file}: {e}")
            # Continue to attempt copy, it might fail with permission denied again

        # shutil.copy2 copies the file data and metadata (like modification times)
        # This operation inherently overwrites 'dest_file' if it already exists.
        shutil.copy2(source_file, dest_file)
        print(f"Copied/Overwrote file: {dest_file}")
      except Exception as e:
        print(f"Error copying {source_file} to {dest_file}: {e}")

  print("\nMerge complete.")


if __name__ == "__main__":
  parser = argparse.ArgumentParser(
    description="Merge the contents of one directory (source) into another (destination), overwriting existing files in the destination."
  )
  parser.add_argument(
    "dir_a",
    type=str,
    help="The destination directory (Dir A) that will be updated.",
  )
  parser.add_argument(
    "dir_b",
    type=str,
    help="The source directory (Dir B) whose contents will be copied over and will take precedence.",
  )

  args = parser.parse_args()

  # B is to be copied over to A (B takes precedence)
  # dir_a is the destination, dir_b is the source.
  merge_directories(args.dir_a, args.dir_b)
