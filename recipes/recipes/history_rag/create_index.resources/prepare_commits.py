# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Prepares and pre-clusters commit data for topic analysis.

This script:
1. Loads commit details from JSON files in a directory
2. Pre-clusters commits that share common bug IDs
3. Cleans commit messages by removing common patterns
4. Sorts commits by date
5. Saves the prepared data to a pickle file

Output format:
    A pickle file containing a list of document dictionaries, where each
    document represents one or more commits grouped by bug ID:
    [
        {
            "message": str,  # Concatenated messages for embedding
            "commits": [     # List of individual commits in this document
                {
                    "hash": str,
                    "message": str,
                    "files": dict,  # {file_path: [(line_num, content), ...]}
                    "date": str,
                    "author": str,
                    "metadata": dict
                },
                ...
            ]
        },
        ...
    ]
"""

import argparse
import json
import pickle
import re
import sys
from pathlib import Path


class UnionFind:
  """A data structure for finding connected components in a graph."""

  def __init__(self, n):
    self.parent = list(range(n))
    self.rank = [0] * n

  def find(self, i):
    if self.parent[i] == i:
      return i
    self.parent[i] = self.find(self.parent[i])  # Path compression
    return self.parent[i]

  def union(self, i, j):
    root_i = self.find(i)
    root_j = self.find(j)
    if root_i != root_j:
      # Union by rank
      if self.rank[root_i] > self.rank[root_j]:
        self.parent[root_j] = root_i
      else:
        self.parent[root_i] = root_j
        if self.rank[root_i] == self.rank[root_j]:
          self.rank[root_j] += 1
      return True
    return False


def load_commit_data(input_dir: Path) -> list[dict]:
  """
  Loads all commit data (message, files, lines) from JSON files in a directory.
  Returns:
      List of commit dictionaries with hash, message, files, date, author, metadata.
  """
  commit_data = []
  json_files = list(input_dir.rglob('*.json'))
  print(f"Found {len(json_files)} commit files to process.")

  for file_path in json_files:
    try:
      with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        data = json.load(f)

      # Validate required fields
      if not all(key in data for key in ['message', 'files', 'date', 'author']):
        print(
          f"Warning: {file_path} missing required fields. Skipping.",
          file=sys.stderr,
        )
        continue

      if not data['message']:
        print(
          f"Warning: {file_path} has empty message. Skipping.", file=sys.stderr
        )
        continue

      commit_data.append(
        {
          "hash": file_path.stem,
          "message": data['message'],
          "files": data['files'],
          "date": data['date'],
          "author": data['author'],
          "metadata": data.get('metadata', {}),
        }
      )
    except (json.JSONDecodeError, IOError) as e:
      print(
        f"Warning: Could not read or parse {file_path}. Skipping. Error: {e}",
        file=sys.stderr,
      )

  return commit_data


def pre_cluster_by_bug_id(all_commits: list[dict]) -> list[dict]:
  """
  Groups commits that share common bug IDs into single documents.

  Uses Union-Find to efficiently group commits that share any bug IDs.
  Each group becomes a single document for embedding.

  Returns:
      List of document dictionaries, each containing:
      - message: Concatenated messages for embedding
      - commits: List of original commit objects
  """
  print("Pre-clustering commits by shared bug IDs...")

  # Build a mapping from bug IDs to commit indices
  bug_to_commits = {}
  for i, commit in enumerate(all_commits):
    metadata = commit.get('metadata', {})
    bug_ids = set(metadata.get('Bug', []) + metadata.get('BUG=', []))
    for bug_id in bug_ids:
      bug_to_commits.setdefault(bug_id, []).append(i)

  # Use Union-Find to group commits that share bug IDs
  uf = UnionFind(len(all_commits))
  for commit_indices in bug_to_commits.values():
    if len(commit_indices) > 1:
      for i in range(1, len(commit_indices)):
        uf.union(commit_indices[0], commit_indices[i])

  # Collect commits by their root
  groups = {}
  for i in range(len(all_commits)):
    root = uf.find(i)
    groups.setdefault(root, []).append(i)

  # Create output documents
  output_documents = []
  for group_indices in groups.values():
    group_commits = [all_commits[i] for i in group_indices]
    # Sort by date within each group
    group_commits.sort(key=lambda c: c.get('date', ''))

    # Concatenate messages for the embedding model
    concatenated_message = "\n---\n".join(c['message'] for c in group_commits)

    output_documents.append(
      {"message": concatenated_message, "commits": group_commits}
    )

  print(
    f"Pre-clustering reduced {len(all_commits)} commits to {len(output_documents)} documents."
  )
  return output_documents


def clean_commit_messages(documents: list[dict]) -> list[dict]:
  """
  Removes common patterns from commit messages that don't add semantic value.
  Patterns removed: "reason", "reverts", "revert", "relands", "reland", "reapply", "commit"
  (case-insensitive)
  """
  print("Cleaning commit messages...")
  pattern = re.compile(
    r"reason|reverts|revert|relands|reland|reapply|commit", re.IGNORECASE
  )

  for doc in documents:
    doc['cleaned_message'] = pattern.sub("", doc['message'])

  return documents


def main():
  """Main function to orchestrate commit preparation."""
  parser = argparse.ArgumentParser(
    description="Prepare and pre-cluster commit data for topic analysis.",
    formatter_class=argparse.RawTextHelpFormatter,
  )
  parser.add_argument(
    "input_dir",
    type=str,
    help="Directory containing the commit detail JSON files.",
  )
  parser.add_argument(
    "--output-file",
    type=str,
    required=True,
    help="Path to save the prepared commits pickle file.",
  )
  parser.add_argument(
    "--limit",
    type=int,
    default=None,
    help="Limit the number of commits to process (for testing).",
  )

  args = parser.parse_args()

  # Validate input directory
  input_dir = Path(args.input_dir).resolve()
  if not input_dir.exists():
    print(
      f"Error: Input directory '{input_dir}' does not exist.", file=sys.stderr
    )
    sys.exit(1)
  if not input_dir.is_dir():
    print(
      f"Error: Input path '{input_dir}' is not a directory.", file=sys.stderr
    )
    sys.exit(1)

  # Load commit data
  all_commits = load_commit_data(input_dir)

  if not all_commits:
    print("No valid commit messages found. Exiting.", file=sys.stderr)
    sys.exit(1)

  # Sort by date (most recent first)
  print("Sorting commits by date...")
  all_commits.sort(key=lambda c: c.get('date', ''), reverse=True)

  # Apply limit if specified
  if args.limit:
    print(f"Limiting to the {args.limit} most recent commits.")
    all_commits = all_commits[: args.limit]

  # Pre-cluster by bug ID
  documents = pre_cluster_by_bug_id(all_commits)

  # Clean commit messages (always performed now)
  documents = clean_commit_messages(documents)

  # Save to pickle file
  output_path = Path(args.output_file).resolve()
  output_path.parent.mkdir(parents=True, exist_ok=True)

  try:
    with open(output_path, 'wb') as f:
      pickle.dump(documents, f, protocol=pickle.HIGHEST_PROTOCOL)

    print(
      f"\nSuccessfully saved {len(documents)} prepared documents to {output_path}"
    )
    print(
      f"Total commits represented: {sum(len(doc['commits']) for doc in documents)}"
    )

  except IOError as e:
    print(f"Error writing to output file {output_path}: {e}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
  main()
