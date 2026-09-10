# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import pickle
import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import numpy as np

import generate_embeddings


class TestGenerateEmbeddings(unittest.TestCase):
  def setUp(self):
    # Create a temporary directory for test files
    self.test_dir = tempfile.mkdtemp()
    self.cache_dir = Path(self.test_dir) / ".embedding_cache"
    self.input_file = Path(self.test_dir) / "commits.pkl"
    self.output_file = Path(self.test_dir) / "output.npz"

    # Create dummy commit data
    self.dummy_commits = [
      {
        'message': 'Fix bug in rendering',
        'cleaned_message': 'Fix bug rendering',
        'commits': ['hash1'],
      },
      {
        'message': 'Update documentation',
        'cleaned_message': 'Update docs',
        'commits': ['hash2'],
      },
      {
        'message': 'Refactor login logic',
        'cleaned_message': 'Refactor login',
        'commits': ['hash3'],
      },
    ]

    # Save dummy data to pickle
    with open(self.input_file, 'wb') as f:
      pickle.dump(self.dummy_commits, f)

  def tearDown(self):
    # cleanup temp directory
    shutil.rmtree(self.test_dir)

  def test_load_prepared_commits_success(self):
    """Test loading valid pickle data."""
    loaded = generate_embeddings.load_prepared_commits(self.input_file)
    self.assertEqual(len(loaded), 3)
    self.assertEqual(loaded[0]['message'], 'Fix bug in rendering')

  def test_load_prepared_commits_file_not_found(self):
    """Test loading a non-existent file."""
    with self.assertRaises(SystemExit):
      generate_embeddings.load_prepared_commits(Path("non_existent.pkl"))

  def test_generate_embeddings_batch_logic(self):
    """Test batching logic and API interaction."""
    mock_client = MagicMock()

    # Create a mock response structure that matches google.genai return type
    def create_mock_response(batch_size):
      mock_response = MagicMock()
      # Create list of objects with .values attribute
      mock_embeddings = []
      for _ in range(batch_size):
        e = MagicMock()
        e.values = [0.1, 0.2]  # Dummy embedding vector
        mock_embeddings.append(e)
      mock_response.embeddings = mock_embeddings
      return mock_response

    mock_client.models.embed_content.side_effect = [
      create_mock_response(2),  # First batch (size 2)
      create_mock_response(1),  # Second batch (size 1 remainder)
    ]

    messages = ["msg1", "msg2", "msg3"]

    # Run with batch size 2 and dummy model name
    embeddings = generate_embeddings.generate_embeddings_batch(
      messages,
      mock_client,
      model_name="test-model",
      output_dimensionality=768,
      batch_size=2,
    )

    self.assertEqual(embeddings.shape, (3, 2))  # 3 messages, dimension 2
    self.assertEqual(mock_client.models.embed_content.call_count, 2)

    call_args = mock_client.models.embed_content.call_args_list[0]
    self.assertEqual(call_args.kwargs['contents'], ["msg1", "msg2"])
    self.assertEqual(call_args.kwargs['model'], "test-model")
    self.assertEqual(call_args.kwargs['config'].output_dimensionality, 768)

  def test_generate_embeddings_retry_logic(self):
    """Test that the function retries on API failure."""
    mock_client = MagicMock()

    # Mock response helper
    mock_response = MagicMock()
    e_obj = MagicMock()
    e_obj.values = [0.1]
    mock_response.embeddings = [e_obj]

    # Fail once, then succeed
    mock_client.models.embed_content.side_effect = [
      Exception("API overloaded"),
      mock_response,
    ]

    # Patch time.sleep to speed up test
    with patch('time.sleep') as mock_sleep:
      embeddings = generate_embeddings.generate_embeddings_batch(
        ["msg1"],
        mock_client,
        model_name="test-model",
        output_dimensionality=768,
        batch_size=1,
        max_retries=3,
      )

    self.assertEqual(len(embeddings), 1)
    self.assertEqual(mock_client.models.embed_content.call_count, 2)
    mock_sleep.assert_called_once()

  def test_cache_logic_full_miss(self):
    """Test scenario where no embeddings are cached."""
    mock_client = MagicMock()

    # Mock API return for 3 items
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=[0.1] * 768) for _ in range(3)]
    mock_client.models.embed_content.return_value = mock_response

    messages = [d['cleaned_message'] for d in self.dummy_commits]

    embeddings = generate_embeddings.get_embeddings_with_cache(
      messages,
      self.cache_dir,
      mock_client,
      model_name="test-model",
      output_dimensionality=768,
    )

    # Should have called API once
    mock_client.models.embed_content.assert_called_once()
    self.assertEqual(embeddings.shape, (3, 768))

    # Verify cache files were created
    cached_files = list((self.cache_dir / "embeddings").glob("*.npy"))
    self.assertEqual(len(cached_files), 3)

  def test_cache_logic_partial_hit(self):
    """Test scenario where some items are already cached."""
    mock_client = MagicMock()
    messages = ["cached_msg", "new_msg"]
    model_name = "test-model"
    output_dim = 768

    cache_key_input = "cached_msg" + model_name + str(output_dim)
    h = hashlib.sha256(cache_key_input.encode('utf-8')).hexdigest()

    cache_path = self.cache_dir / "embeddings" / f"{h}.npy"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache_path, np.array([0.9] * 768))

    # Mock API return for the ONE missing item
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=[0.1] * 768)]
    mock_client.models.embed_content.return_value = mock_response

    embeddings = generate_embeddings.get_embeddings_with_cache(
      messages,
      self.cache_dir,
      mock_client,
      model_name=model_name,
      output_dimensionality=output_dim,
    )

    mock_client.models.embed_content.assert_called_once()
    called_messages = mock_client.models.embed_content.call_args.kwargs[
      'contents'
    ]
    self.assertEqual(called_messages, ["new_msg"])

    # Result should combine cached (0.9) and new (0.1)
    self.assertAlmostEqual(embeddings[0][0], 0.9)
    self.assertAlmostEqual(embeddings[1][0], 0.1)

  @patch('gemini_client.initialize_gemini_client')
  def test_main_flow(self, mock_init_client):
    """Test the end-to-end main function."""
    # Setup mocks
    mock_client = MagicMock()
    mock_init_client.return_value = mock_client

    # Mock API response
    mock_resp = MagicMock()
    # 3 documents in dummy data
    mock_resp.embeddings = [MagicMock(values=[0.5] * 10) for _ in range(3)]
    mock_client.models.embed_content.return_value = mock_resp

    # Arguments simulation
    test_args = [
      "generate_embeddings.py",
      str(self.input_file),
      "--output-file",
      str(self.output_file),
      "--cache-dir",
      str(self.cache_dir),
    ]

    with patch.object(sys, 'argv', test_args):
      generate_embeddings.main()

    # Verify output file exists and structure
    self.assertTrue(self.output_file.exists())

    with np.load(self.output_file, allow_pickle=True) as data:
      self.assertIn('embeddings', data)
      self.assertIn('metadata', data)
      self.assertEqual(data['embeddings'].shape, (3, 10))

      # Check metadata
      meta = data['metadata'][0]
      self.assertEqual(meta['num_documents'], 3)
      self.assertEqual(meta['model'], 'gemini-embedding-001')


if __name__ == '__main__':
  unittest.main()
