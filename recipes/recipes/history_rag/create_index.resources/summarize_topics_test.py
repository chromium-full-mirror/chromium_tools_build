# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import numpy as np
import summarize_topics


class TestSummarizeTopics(unittest.TestCase):

  def setUp(self):
    # Create a temporary directory for cache testing
    self.test_dir = tempfile.mkdtemp()
    self.test_dir_path = Path(self.test_dir)
    self.cache_dir = self.test_dir_path / "cache"
    self.cache_dir.mkdir()

    # Create a dummy client for passing around
    self.mock_client = MagicMock()

  def tearDown(self):
    # Clean up temporary directory
    shutil.rmtree(self.test_dir)

  # --- Tests for Utility Functions ---

  def test_chunk_summary_simple(self):
    """Test that small strings return as a single chunk."""
    text = "Hello\nWorld"
    chunks = summarize_topics.chunk_summary(text, chunk_size=50)
    self.assertEqual(chunks, ["Hello\nWorld"])

  def test_chunk_summary_splitting(self):
    """Test that strings are split at newlines after chunk_size."""
    # Create a string where the first newline is after chunk_size=5
    # "12345" (len 5) -> next char is '\n', so it should split there.
    text = "12345\n67890\nABCDE"
    chunks = summarize_topics.chunk_summary(text, chunk_size=5)
    self.assertEqual(chunks, ["12345", "67890", "ABCDE"])

  def test_chunk_summary_no_newlines(self):
    """Test behavior when no newline is found (returns whole string)."""
    text = "1234567890"
    chunks = summarize_topics.chunk_summary(text, chunk_size=5)
    self.assertEqual(chunks, ["1234567890"])

  def test_chunk_summary_empty(self):
    """Test empty input."""
    self.assertEqual(summarize_topics.chunk_summary(""), [])

  def test_format_code_context(self):
    """Test aggregation and sorting of code context lines."""
    commits = [
        {
            'files': {
                'utils.py': [(10, 'def helper():'), (1, 'import sys')],
                'main.py': [(5, 'def main():')]
            }
        },
        {
            'files': {
                'utils.py': [(2, 'import os')
                            ]  # Should interleave with utils.py above
            }
        }
    ]

    expected_output = ("\nmain.py\n"
                       "5:def main():\n"
                       "\nutils.py\n"
                       "1:import sys\n"
                       "2:import os\n"
                       "10:def helper():")

    result = summarize_topics.format_code_context(commits)
    self.assertEqual(result, expected_output)

  # --- Tests for File I/O ---

  def test_load_clustering_results_success(self):
    """Test successful JSON loading."""
    data = {"metadata": {}, "topics": [{"id": 1}]}
    json_content = json.dumps(data)

    with patch("builtins.open", mock_open(read_data=json_content)):
      result = summarize_topics.load_clustering_results(Path("dummy.json"))
      self.assertEqual(result, data)

  def test_load_clustering_results_file_not_found(self):
    """Test that the script exits if file is missing."""
    with patch("builtins.open", side_effect=IOError):
      with self.assertRaises(SystemExit):
        summarize_topics.load_clustering_results(Path("missing.json"))

  def test_load_clustering_results_bad_json(self):
    """Test that the script exits on malformed JSON."""
    with patch("builtins.open", mock_open(read_data="{invalid_json")):
      with self.assertRaises(SystemExit):
        summarize_topics.load_clustering_results(Path("bad.json"))

  # --- Tests for LLM Interaction & Caching ---

  @patch("summarize_topics.hashlib.sha256")
  def test_call_llm_with_cache_hit(self, mock_hash):
    """Test that we read from file and skip API call on cache hit."""
    mock_hash.return_value.hexdigest.return_value = "testhash"

    # Create a fake cache entry
    cache_file = self.cache_dir / "testhash.txt"
    cache_file.write_text("Cached Response", encoding='utf-8')

    result = summarize_topics.call_llm_with_cache("prompt", "model",
                                                  self.mock_client,
                                                  self.cache_dir)

    self.assertEqual(result, "Cached Response")
    self.mock_client.models.generate_content.assert_not_called()

  @patch("summarize_topics.hashlib.sha256")
  def test_call_llm_with_cache_miss(self, mock_hash):
    """Test that we call API and write to file on cache miss."""
    mock_hash.return_value.hexdigest.return_value = "testhash"

    # Mock API response
    mock_response = MagicMock()
    mock_response.candidates[0].content.parts[0].text = "API Response"
    self.mock_client.models.generate_content.return_value = mock_response

    result = summarize_topics.call_llm_with_cache("prompt", "model",
                                                  self.mock_client,
                                                  self.cache_dir)

    # Verify result and API call
    self.assertEqual(result, "API Response")
    self.mock_client.models.generate_content.assert_called_once()

    # Verify file was written
    cache_file = self.cache_dir / "testhash.txt"
    self.assertTrue(cache_file.exists())
    self.assertEqual(cache_file.read_text(encoding='utf-8'), "API Response")

  @patch("summarize_topics.call_llm_with_cache")
  def test_get_topic_summary_and_title_parsing(self, mock_call_llm):
    """Test parsing of the Title\nSummary format."""
    mock_call_llm.return_value = ' "My Title" \n This is the summary. '

    title, summary = summarize_topics.get_topic_summary_and_title(
        ["msg"], "model", self.mock_client, self.cache_dir)

    self.assertEqual(title, "My Title")
    self.assertEqual(summary, "This is the summary.")

  # --- Tests for Embeddings ---

  @patch("summarize_topics.np.save")
  @patch("summarize_topics.np.load")
  def test_get_summary_embedding_cache_miss(self, mock_load, mock_save):
    """Test embedding API call on cache miss."""
    # Mock load to raise IOError (cache miss)
    mock_load.side_effect = IOError

    # Mock API response
    mock_result = MagicMock()
    mock_result.embeddings = [MagicMock(values=[0.1, 0.2, 0.3])]
    self.mock_client.models.embed_content.return_value = mock_result

    embedding = summarize_topics.get_summary_embedding_with_cache(
        "text",
        self.cache_dir,
        self.mock_client,
        "embedding-model",
        output_dimensionality=768)

    self.assertEqual(embedding, [0.1, 0.2, 0.3])
    self.mock_client.models.embed_content.assert_called_once()
    mock_save.assert_called_once()

  # --- Integration Logic Tests ---

  @patch("summarize_topics.get_summary_embedding_with_cache")
  @patch("summarize_topics.get_topic_summary_and_title")
  def test_process_topic_full_flow(self, mock_get_summary, mock_get_embedding):
    """Test the main processing function (summarization + embedding + cleaning)."""

    # Setup mocks
    mock_get_summary.return_value = ("Title", "Summary text")
    mock_get_embedding.return_value = [1.0, 0.0]

    # Input data
    topic_data = {
        'topic_id':
            123,
        'commit_count':
            5,
        'commits': [{
            'date': '2025-01-01',
            'message': 'msg1',
            'metadata': {},
            'files': {
                'test.py': [(1, 'a = 1')]
            }
        }]
    }

    # Run process_topic
    result = summarize_topics.process_topic(
        topic_data,
        "llm-model-name",
        "embedding-model-name",
        self.mock_client,
        self.cache_dir,
        self.cache_dir,
        output_dimensionality=768)

    # Assertions
    self.assertEqual(result['topic_id'], 123)
    self.assertEqual(result['title'], "Title")
    self.assertEqual(result['summary'], "Summary text")
    self.assertEqual(result['chunk_embeddings'], [[1.0, 0.0]])

    # Check that code context was generated
    self.assertIn("test.py", result['code_context'])
    self.assertIn("1:a = 1", result['code_context'])

    # IMPORTANT: Check that 'files' key was removed from commits to save space
    self.assertNotIn('files', result['commits'][0])

    mock_get_embedding.assert_called_with('Summary text', self.cache_dir,
                                          self.mock_client,
                                          'embedding-model-name', 768)

  def test_extract_topic_keywords(self):
    """Test keyword extraction returns correct structure."""
    topics = [{
        'topic_id': 1,
        'commits': [{
            'message': "fix bug"
        }, {
            'message': "fix another bug"
        }]
    }, {
        'topic_id': 2,
        'commits': [{
            'message': "feature add"
        }]
    }]

    with patch("summarize_topics.CountVectorizer") as mock_cv:
      mock_instance = mock_cv.return_value

      # Prepare a mock for the matrix returned by fit_transform
      mock_X = MagicMock()

      # Define what X.toarray() returns (dense matrix)
      mock_X.toarray.return_value = np.array([[1, 0], [0, 1]])

      # Define what (X > 0) returns (boolean mask for c-TF-IDF calculation)
      # We just return a numpy boolean array so .sum(axis=0) works
      mock_X.__gt__.return_value = np.array([[True, False], [False, True]])

      mock_instance.fit_transform.return_value = mock_X
      mock_instance.get_feature_names_out.return_value = ["bug", "feature"]

      # Call the function
      keywords = summarize_topics.extract_topic_keywords(topics)

      self.assertIsInstance(keywords, dict)
      self.assertIn(1, keywords)
      self.assertIn(2, keywords)


if __name__ == "__main__":
  unittest.main()
