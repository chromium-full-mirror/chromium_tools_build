# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import unittest
import json
import pickle
import shutil
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from io import StringIO

import prepare_commits


class TestUnionFind(unittest.TestCase):
  def test_union_find_basics(self):
    # Test logic for 5 elements: 0, 1, 2, 3, 4
    uf = prepare_commits.UnionFind(5)

    # Initially everyone is their own parent
    self.assertEqual(uf.find(0), 0)
    self.assertEqual(uf.find(4), 4)

    # Union 0 and 1
    self.assertTrue(uf.union(0, 1))
    self.assertEqual(uf.find(0), uf.find(1))
    self.assertNotEqual(uf.find(0), uf.find(2))

    # Union 2 and 3
    uf.union(2, 3)

    # Union 1 and 3 (connecting the two groups: 0-1 and 2-3)
    uf.union(1, 3)

    # Now 0, 1, 2, 3 should all have the same root
    root = uf.find(0)
    self.assertEqual(uf.find(1), root)
    self.assertEqual(uf.find(2), root)
    self.assertEqual(uf.find(3), root)

    # 4 should still be separate
    self.assertNotEqual(uf.find(4), root)

  def test_union_existing_connection(self):
    uf = prepare_commits.UnionFind(2)
    uf.union(0, 1)
    # Returns False because they are already connected
    self.assertFalse(uf.union(0, 1))


class TestCleaning(unittest.TestCase):
  def test_clean_commit_messages(self):
    input_docs = [
      {"message": "Revert 'Fix bug' because of reason X", "commits": []},
      {"message": "Reland: Add feature Y", "commits": []},
      {"message": "Commit message clean", "commits": []},
      {"message": "This REVERTS everything", "commits": []},
    ]

    cleaned = prepare_commits.clean_commit_messages(input_docs)

    # "Revert", "reason" removed
    self.assertEqual(
      cleaned[0]['cleaned_message'].strip(), "'Fix bug' because of  X"
    )
    # "Reland" removed
    self.assertEqual(cleaned[1]['cleaned_message'].strip(), ": Add feature Y")
    # "Commit" removed
    self.assertEqual(cleaned[2]['cleaned_message'].strip(), "message clean")
    # Case insensitive check
    self.assertEqual(cleaned[3]['cleaned_message'].strip(), "This  everything")


class TestClustering(unittest.TestCase):
  def setUp(self):
    self.base_commit = {
      "hash": "hash",
      "message": "msg",
      "files": {},
      "date": "2025-01-01",
      "author": "me",
      "metadata": {},
    }

  def test_clustering_no_shared_bugs(self):
    commits = [
      {**self.base_commit, "hash": "1", "metadata": {"Bug": ["100"]}},
      {**self.base_commit, "hash": "2", "metadata": {"Bug": ["200"]}},
    ]
    docs = prepare_commits.pre_cluster_by_bug_id(commits)
    self.assertEqual(len(docs), 2)

  def test_clustering_direct_share(self):
    commits = [
      {**self.base_commit, "hash": "1", "metadata": {"Bug": ["100"]}},
      {**self.base_commit, "hash": "2", "metadata": {"Bug": ["100"]}},
    ]
    docs = prepare_commits.pre_cluster_by_bug_id(commits)
    self.assertEqual(len(docs), 1)
    self.assertEqual(len(docs[0]['commits']), 2)

  def test_clustering_transitive_share(self):
    # Commit 1 has Bug 100
    # Commit 2 has Bug 200
    # Commit 3 has Bug 100 AND 200 (Connecting 1 and 2)
    commits = [
      {**self.base_commit, "hash": "1", "metadata": {"Bug": ["100"]}},
      {**self.base_commit, "hash": "2", "metadata": {"Bug": ["200"]}},
      {**self.base_commit, "hash": "3", "metadata": {"Bug": ["100", "200"]}},
    ]
    docs = prepare_commits.pre_cluster_by_bug_id(commits)
    self.assertEqual(len(docs), 1)
    self.assertEqual(len(docs[0]['commits']), 3)

  def test_clustering_handles_bug_vs_BUG(self):
    # Test normalization of "Bug" vs "BUG=" keys
    commits = [
      {**self.base_commit, "hash": "1", "metadata": {"Bug": ["123"]}},
      {**self.base_commit, "hash": "2", "metadata": {"BUG=": ["123"]}},
    ]
    docs = prepare_commits.pre_cluster_by_bug_id(commits)
    self.assertEqual(len(docs), 1)


class TestFileIO(unittest.TestCase):
  def setUp(self):
    self.test_dir = tempfile.mkdtemp()
    self.input_path = Path(self.test_dir)

  def tearDown(self):
    shutil.rmtree(self.test_dir)

  def test_load_commit_data_success(self):
    # Create a valid json file
    data = {
      "message": "Test Message",
      "files": {"a.py": []},
      "date": "2025-01-01",
      "author": "user@google.com",
      "metadata": {},
    }
    with open(self.input_path / "valid.json", 'w', encoding='utf-8') as f:
      json.dump(data, f)

    results = prepare_commits.load_commit_data(self.input_path)
    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]['hash'], "valid")

  def test_load_commit_data_skips_invalid(self):
    # 1. Missing fields
    with open(self.input_path / "missing.json", 'w', encoding='utf-8') as f:
      json.dump({"message": "only message"}, f)

    # 2. Empty message
    with open(self.input_path / "empty_msg.json", 'w', encoding='utf-8') as f:
      json.dump({"message": "", "files": {}, "date": "x", "author": "x"}, f)

    # 3. Bad JSON
    with open(self.input_path / "bad.json", 'w', encoding='utf-8') as f:
      f.write("{ invalid json }")

    # 4. Valid one
    with open(self.input_path / "valid.json", 'w', encoding='utf-8') as f:
      json.dump({"message": "ok", "files": {}, "date": "x", "author": "x"}, f)

    # Capture stderr to keep test output clean
    with patch('sys.stderr', new=StringIO()):
      results = prepare_commits.load_commit_data(self.input_path)

    self.assertEqual(len(results), 1)
    self.assertEqual(results[0]['hash'], "valid")


class TestIntegration(unittest.TestCase):
  def setUp(self):
    # Use mkdtemp instead of TemporaryDirectory to avoid Pylint R1732
    # (consider-using-with) since we need to persist it for the tests
    # and clean up in tearDown.
    self.temp_dir = tempfile.mkdtemp()
    self.input_dir = Path(self.temp_dir) / "input"
    self.input_dir.mkdir()
    self.output_file = Path(self.temp_dir) / "output.pkl"

    # Create some test data
    # Two commits sharing bug 100 -> 1 document
    # One commit with unique bug -> 1 document
    c1 = {
      "message": "Fix bug 100",
      "files": {},
      "date": "2025-01-02",
      "author": "a",
      "metadata": {"Bug": ["100"]},
    }
    c2 = {
      "message": "Revert fix 100",
      "files": {},
      "date": "2025-01-03",
      "author": "b",
      "metadata": {"Bug": ["100"]},
    }
    c3 = {
      "message": "Feature 200",
      "files": {},
      "date": "2025-01-01",
      "author": "c",
      "metadata": {"Bug": ["200"]},
    }

    with open(self.input_dir / "c1.json", 'w', encoding='utf-8') as f:
      json.dump(c1, f)
    with open(self.input_dir / "c2.json", 'w', encoding='utf-8') as f:
      json.dump(c2, f)
    with open(self.input_dir / "c3.json", 'w', encoding='utf-8') as f:
      json.dump(c3, f)

  def tearDown(self):
    shutil.rmtree(self.temp_dir)

  def test_main_flow(self):
    test_args = [
      "prepare_commits.py",
      str(self.input_dir),
      "--output-file",
      str(self.output_file),
    ]

    with patch.object(sys, 'argv', test_args):
      prepare_commits.main()

    self.assertTrue(self.output_file.exists())

    with open(self.output_file, 'rb') as f:
      docs = pickle.load(f)

    # Should have 2 documents (Bug 100 group, Bug 200 group)
    self.assertEqual(len(docs), 2)

    # Find the bug 100 group
    bug_100_doc = next(d for d in docs if len(d['commits']) == 2)

    # Check cleaning happened (Revert should be stripped from c2)
    # c2 message was "Revert fix 100" -> " fix 100"
    commits_msgs = [c['message'] for c in bug_100_doc['commits']]
    self.assertIn("Revert fix 100", commits_msgs)
    self.assertIn(" fix 100", bug_100_doc['cleaned_message'])

  def test_main_limit_argument(self):
    test_args = [
      "prepare_commits.py",
      str(self.input_dir),
      "--output-file",
      str(self.output_file),
      "--limit",
      "1",
    ]

    with patch.object(sys, 'argv', test_args):
      prepare_commits.main()

    with open(self.output_file, 'rb') as f:
      docs = pickle.load(f)

    # We limited to 1 commit, so max 1 document
    total_commits = sum(len(d['commits']) for d in docs)
    self.assertEqual(total_commits, 1)


if __name__ == '__main__':
  unittest.main()
