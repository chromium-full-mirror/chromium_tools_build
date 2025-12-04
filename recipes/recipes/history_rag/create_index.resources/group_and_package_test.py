# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import unittest
from unittest.mock import MagicMock, patch, mock_open, call, ANY
import sys
import json
import pickle
import io
from pathlib import Path
import numpy as np

import group_and_package


class TestGroupAndPackage(unittest.TestCase):

  def setUp(self):
    # Common test data
    self.sample_topics = {
        "metadata": {
            "version": "1.0"
        },
        "topics": [{
            "topic_id": 1,
            "title": "Fix memory leak",
            "summary": "Fixes a leak in render.",
            "summary_chunks": ["Chunk 1"],
            "chunk_embeddings": [[0.1, 0.2]],
            "code_context": "void func() {}",
            "commits": [],
            "commit_count": 1
        }, {
            "topic_id": 2,
            "title": "Update README",
            "summary": "Updates docs.",
            "summary_chunks": ["Chunk A"],
            "chunk_embeddings": [[0.9, 0.8]],
            "code_context": "README.md",
            "commits": [],
            "commit_count": 1
        }]
    }

  # -------------------------------------------------------------------------
  # Test: load_summarized_topics
  # -------------------------------------------------------------------------

  @patch("builtins.open", new_callable=mock_open, read_data='{"topics": []}')
  def test_load_summarized_topics_success(self, mock_file):
    result = group_and_package.load_summarized_topics(Path("dummy.json"))
    self.assertEqual(result, {"topics": []})
    mock_file.assert_called_once()

  @patch("builtins.open", side_effect=IOError("File not found"))
  def test_load_summarized_topics_io_error(self, mock_file):
    with self.assertRaises(SystemExit):
      group_and_package.load_summarized_topics(Path("missing.json"))

  @patch("builtins.open", new_callable=mock_open, read_data="INVALID JSON")
  def test_load_summarized_topics_json_error(self, mock_file):
    with self.assertRaises(SystemExit):
      group_and_package.load_summarized_topics(Path("bad.json"))


  # -------------------------------------------------------------------------
  # Test: call_llm_with_cache
  # -------------------------------------------------------------------------

  @patch("pathlib.Path.exists", return_value=True)
  @patch("builtins.open", new_callable=mock_open, read_data="Cached Response")
  def test_call_llm_cache_hit(self, mock_file, mock_exists):
    client = MagicMock()
    result = group_and_package.call_llm_with_cache("prompt", "model", client,
                                                   Path("./cache"))
    self.assertEqual(result, "Cached Response")
    # Client should NOT be called on cache hit
    client.models.generate_content.assert_not_called()

  @patch("pathlib.Path.exists", return_value=False)
  @patch("builtins.open", new_callable=mock_open)
  def test_call_llm_cache_miss_success(self, mock_file, mock_exists):
    client = MagicMock()
    mock_response = MagicMock()
    mock_response.candidates[0].content.parts[0].text = " API Response "
    client.models.generate_content.return_value = mock_response

    result = group_and_package.call_llm_with_cache("prompt", "model", client,
                                                   Path("./cache"))

    self.assertEqual(result, "API Response")
    client.models.generate_content.assert_called_once()
    # Should write to cache
    mock_file().write.assert_called_with("API Response")

  @patch("group_and_package.time.sleep")  # Mock sleep to speed up retries
  @patch("pathlib.Path.exists", return_value=False)
  def test_call_llm_retries_and_fail(self, mock_exists, mock_sleep):
    client = MagicMock()
    client.models.generate_content.side_effect = Exception("API Error")

    # Capture stderr to avoid cluttering test output
    with patch("sys.stderr", new=io.StringIO()):
      result = group_and_package.call_llm_with_cache("prompt", "model", client,
                                                     Path("./cache"))

    self.assertIsNone(result)
    # Should retry 10 times
    self.assertEqual(client.models.generate_content.call_count, 10)

  # -------------------------------------------------------------------------
  # Test: generate_group_categories
  # -------------------------------------------------------------------------

  @patch("group_and_package.call_llm_with_cache")
  def test_generate_group_categories_success(self, mock_call_llm):
    mock_call_llm.return_value = "Category A\nCategory B\nCategory C"
    topics = [{"topic_id": 1, "title": "T1"}, {"topic_id": 2, "title": "T2"}]

    groups = group_and_package.generate_group_categories(
        topics, "model", MagicMock(), Path("./"), num_groups=3)

    self.assertEqual(groups, ["Category A", "Category B", "Category C"])

  @patch("group_and_package.call_llm_with_cache")
  def test_generate_group_categories_retry_on_count_mismatch(
      self, mock_call_llm):
    # First call returns 2, Second call returns 3 (desired)
    mock_call_llm.side_effect = ["Cat A\nCat B", "Cat A\nCat B\nCat C"]
    topics = [{"topic_id": 1, "title": "T1"}]

    with patch("group_and_package.time.sleep"):  # Mock sleep
      groups = group_and_package.generate_group_categories(
          topics, "model", MagicMock(), Path("./"), num_groups=3)

    self.assertEqual(len(groups), 3)
    self.assertEqual(mock_call_llm.call_count, 2)

  # -------------------------------------------------------------------------
  # Test: group_topics_with_fixed_categories
  # -------------------------------------------------------------------------

  @patch("group_and_package.call_llm_with_cache")
  def test_group_topics_fixed_categories(self, mock_call_llm):
    # Mock LLM returning JSON mapping
    mock_response = json.dumps({"Performance": [1], "Documentation": [2]})
    mock_call_llm.return_value = mock_response

    topics = self.sample_topics["topics"]  # IDs 1 and 2
    fixed_cats = ["Performance", "Documentation", "Security"]

    group_and_package.group_topics_with_fixed_categories(
        topics, "model", MagicMock(), Path("./"), 1, fixed_cats)

    self.assertEqual(topics[0]["group"], "Performance")
    self.assertEqual(topics[1]["group"], "Documentation")

  @patch("group_and_package.call_llm_with_cache")
  def test_group_topics_handles_json_markdown_format(self, mock_call_llm):
    # LLMs often return ```json ... ```
    mock_response = "```json\n{\"Performance\": [1], \"Documentation\": [2]}\n```"
    mock_call_llm.return_value = mock_response

    topics = self.sample_topics["topics"]
    fixed_cats = ["Performance", "Documentation"]

    group_and_package.group_topics_with_fixed_categories(
        topics, "model", MagicMock(), Path("./"), 1, fixed_cats)

    self.assertEqual(topics[0]["group"], "Performance")

  # -------------------------------------------------------------------------
  # Test: package_topics_to_zip
  # -------------------------------------------------------------------------

  @patch("zipfile.ZipFile")
  @patch("numpy.save")
  def test_package_topics_to_zip(self, mock_np_save, mock_zip_cls):
    # Setup mock zip
    mock_zip_instance = MagicMock()
    mock_zip_cls.return_value.__enter__.return_value = mock_zip_instance

    topics = self.sample_topics["topics"]
    # Ensure topic has group info
    topics[0]["group"] = "A"
    topics[1]["group"] = "B"

    output_path = MagicMock()
    output_path.stat.return_value.st_size = 1024  # Mock file size for print

    group_and_package.package_topics_to_zip(topics, output_path,
                                            "Analysis Name", {"meta": "data"})

    # 1. Verify index.pkl was written
    # Find the call to writestr('index.pkl', ...)
    index_call = None
    for call_args in mock_zip_instance.writestr.call_args_list:
      if call_args[0][0] == 'index.pkl':
        index_call = call_args
        break

    self.assertIsNotNone(index_call)
    # Decode pickle to verify contents
    pkl_content = pickle.loads(index_call[0][1])
    self.assertEqual(pkl_content["name"], "Analysis Name")
    self.assertEqual(len(pkl_content["topics"]), 2)
    self.assertEqual(pkl_content["topics"][0]["group"], "A")

    # 2. Verify embeddings.npy was written
    # Verify numpy save was called
    mock_np_save.assert_called()
    # Verify zip wrote embeddings.npy
    embeddings_call = any(c[0][0] == 'embeddings.npy'
                          for c in mock_zip_instance.writestr.call_args_list)
    self.assertTrue(embeddings_call)

    # 3. Verify individual topic JSONs were written
    topic_1_call = any(c[0][0] == 'topics/topic_1.json'
                       for c in mock_zip_instance.writestr.call_args_list)
    self.assertTrue(topic_1_call)

  # -------------------------------------------------------------------------
  # Test: Main Integration (ArgParse)
  # -------------------------------------------------------------------------

  @patch("group_and_package.load_summarized_topics")
  @patch("group_and_package.package_topics_to_zip")
  @patch("group_and_package.Path.exists", return_value=True)
  def test_main_no_grouping(self, mock_exists, mock_pkg, mock_load):
    # Simulate command line args
    test_args = [
        "script_name", "input.json", "--output-file", "out.zip", "--no-grouping"
    ]

    mock_load.return_value = self.sample_topics

    with patch.object(sys, 'argv', test_args):
      group_and_package.main()

    # Verify topics were marked Uncategorized
    loaded_topics = mock_load.return_value["topics"]
    self.assertEqual(loaded_topics[0]["group"], "Uncategorized")

    # Verify package called
    mock_pkg.assert_called_once()

  @patch("group_and_package.load_summarized_topics")
  @patch("gemini_client.initialize_gemini_client")
  @patch("group_and_package.generate_group_categories")
  @patch("group_and_package.group_topics_with_fixed_categories")
  @patch("group_and_package.package_topics_to_zip")
  @patch("group_and_package.Path.exists", return_value=True)
  def test_main_gen_groups(self, mock_exists, mock_pkg, mock_group, mock_gen,
                           mock_init, mock_load):
    test_args = [
        "script_name", "input.json", "--output-file", "out.zip", "--gen-groups",
        "5"
    ]

    mock_load.return_value = self.sample_topics
    mock_gen.return_value = ["A", "B", "C", "D", "E"]

    with patch.object(sys, 'argv', test_args):
      group_and_package.main()

    mock_init.assert_called_once()
    mock_gen.assert_called_once()
    mock_group.assert_called_once()
    mock_pkg.assert_called_once()


if __name__ == '__main__':
  unittest.main()
