# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import unittest
import sys
import pickle
import json
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import cluster_topics


class TestClusterTopics(unittest.TestCase):
  def setUp(self):
    # Create dummy embeddings and metadata for reuse
    self.dummy_embeddings = np.random.rand(10, 768)
    self.dummy_metadata = {'model': 'test_model'}

    # Create dummy documents
    self.dummy_documents = []
    for i in range(10):
      self.dummy_documents.append(
        {
          'id': i,
          'commits': [
            {
              'hash': f'hash_{i}',
              'date': '2025-01-01',
              'files': {
                'foo.py': [(1, 'import os'), (2, 'print("hello")')],
                'bar.go': [(10, 'func main() {}')],
              },
            }
          ],
        }
      )

  # -------------------------------------------------------------------------
  # File Loading Tests
  # -------------------------------------------------------------------------

  @patch('numpy.load')
  def test_load_embeddings_success(self, mock_np_load):
    # Setup mock
    mock_np_load.return_value = {
      'embeddings': self.dummy_embeddings,
      'metadata': [self.dummy_metadata],
    }

    path = Path('dummy.npz')
    emb, meta = cluster_topics.load_embeddings(path)

    self.assertTrue(np.array_equal(emb, self.dummy_embeddings))
    self.assertEqual(meta, self.dummy_metadata)
    mock_np_load.assert_called_once()

  @patch('numpy.load')
  def test_load_embeddings_failure(self, mock_np_load):
    mock_np_load.side_effect = IOError("File not found")

    # Expect system exit on failure
    with self.assertRaises(SystemExit) as cm:
      cluster_topics.load_embeddings(Path('bad.npz'))
    self.assertEqual(cm.exception.code, 1)

  def test_load_prepared_commits_success(self):
    mock_data = [{'id': 1}]
    with (
      patch('builtins.open', mock_open(read_data=pickle.dumps(mock_data))),
      patch('pickle.load', return_value=mock_data),
    ):
      docs = cluster_topics.load_prepared_commits(Path('commits.pkl'))
      self.assertEqual(docs, mock_data)

  # -------------------------------------------------------------------------
  # ML/Clustering Wrapper Tests
  # -------------------------------------------------------------------------

  def test_perform_dimensionality_reduction(self):
    with patch('cluster_topics.umap') as mock_umap:
      mock_reducer = MagicMock()
      mock_reducer.fit_transform.return_value = np.zeros((10, 5))
      mock_umap.UMAP.return_value = mock_reducer

      result = cluster_topics.perform_dimensionality_reduction(
        self.dummy_embeddings, n_neighbors=5, n_components=5
      )

      self.assertEqual(result.shape, (10, 5))
      mock_umap.UMAP.assert_called_once_with(
        n_neighbors=5,
        n_components=5,
        min_dist=0.0,
        metric='cosine',
        random_state=42,
        verbose=True,
      )

  def test_perform_hdbscan_clustering(self):
    # FIX: Patch 'cluster_topics.hdbscan' directly
    with patch('cluster_topics.hdbscan') as mock_hdbscan:
      mock_clusterer = MagicMock()
      # 8 clusters, 2 outliers (-1)
      mock_clusterer.labels_ = np.array([0, 0, 0, 1, 1, 1, -1, -1, 2, 2])
      mock_hdbscan.HDBSCAN.return_value = mock_clusterer

      labels = cluster_topics.perform_hdbscan_clustering(
        np.zeros((10, 5)), min_cluster_size=2, min_samples=2
      )

      self.assertTrue(np.array_equal(labels, mock_clusterer.labels_))

  def test_assign_outliers_with_kmeans(self):
    # FIX: Patch 'cluster_topics.KMeans' because it is imported directly
    with patch('cluster_topics.KMeans') as mock_kmeans_class:
      mock_kmeans_instance = MagicMock()
      # Predict outliers to belong to cluster 0 and 1
      mock_kmeans_instance.predict.return_value = np.array([0, 1])
      mock_kmeans_class.return_value = mock_kmeans_instance

      reduced_emb = np.random.rand(5, 2)
      # 0,1 are cluster 0. 2 is cluster 1. 3,4 are outliers
      initial_labels = np.array([0, 0, 1, -1, -1])

      new_labels = cluster_topics.assign_outliers_with_kmeans(
        reduced_emb, initial_labels
      )

      # Indices 3 and 4 should now be 0 and 1 (based on mock prediction)
      self.assertEqual(new_labels[3], 0)
      self.assertEqual(new_labels[4], 1)
      # Original labels should remain
      self.assertEqual(new_labels[0], 0)

  # -------------------------------------------------------------------------
  # Formatting and Logic Tests
  # -------------------------------------------------------------------------

  def test_format_code_context(self):
    commits = [
      {
        'files': {
          'a.py': [(10, 'line10'), (20, 'line20')],
          'b.py': [(5, 'line5')],
        }
      },
      {
        'files': {
          'a.py': [(15, 'line15')]  # Merges into a.py
        }
      },
    ]

    result = cluster_topics.format_code_context(commits)

    # Check for presence of file names and sorted lines
    self.assertIn('\na.py', result)
    self.assertIn('10:line10', result)
    self.assertIn('15:line15', result)
    self.assertIn('20:line20', result)
    self.assertIn('\nb.py', result)
    self.assertIn('5:line5', result)

  def test_create_topics_from_labels(self):
    labels = np.array(
      [0, 1, 0, -1]
    )  # Doc 0->T0, Doc 1->T1, Doc 2->T0, Doc 3->Outlier
    docs = [
      {'commits': [{'date': '2025-01-02', 'files': {}}]},
      {'commits': [{'date': '2025-01-01', 'files': {}}]},
      {'commits': [{'date': '2025-01-03', 'files': {}}]},
      {'commits': [{'date': '2025-01-04', 'files': {}}]},
    ]

    topics = cluster_topics.create_topics_from_labels(labels, docs)

    # Should have 2 topics (outlier excluded)
    self.assertEqual(len(topics), 2)

    # Find topic 0
    t0 = next(t for t in topics if t['topic_id'] == 0)
    self.assertEqual(len(t0['document_indices']), 2)
    self.assertIn(0, t0['document_indices'])
    self.assertIn(2, t0['document_indices'])
    # Check date sorting in T0
    self.assertEqual(t0['commits'][0]['date'], '2025-01-02')
    self.assertEqual(t0['commits'][1]['date'], '2025-01-03')

  # -------------------------------------------------------------------------
  # Splitting Logic Tests
  # -------------------------------------------------------------------------

  def test_split_topic_by_code_context(self):
    # Generate 3 commits, each roughly 50 chars of context
    c1 = {'files': {'f1': [(1, 'a' * 40)]}, 'date': '1'}
    c2 = {'files': {'f1': [(2, 'b' * 40)]}, 'date': '2'}
    c3 = {'files': {'f1': [(3, 'c' * 40)]}, 'date': '3'}

    # Set max context so only 1 commit fits per chunk
    # Context structure is roughly: "\nf1\n1:aaaa..."
    max_context = 60

    chunks = cluster_topics.split_topic_by_code_context(
      [c1, c2, c3], max_context
    )

    self.assertEqual(len(chunks), 3)
    self.assertEqual(chunks[0][0], c1)
    self.assertEqual(chunks[1][0], c2)

  def test_split_oversized_topics(self):
    # Create a topic with enough commits to force a split
    # Assuming formatting overhead, 'a'*100 is > 50 chars
    c1 = {'files': {'f': [(1, 'a' * 100)]}, 'date': '1'}
    c2 = {'files': {'f': [(2, 'b' * 100)]}, 'date': '2'}

    original_topics = [
      {
        'topic_id': 1,
        'document_indices': [0, 1],
        'commits': [c1, c2],
        'commit_count': 2,
        'code_context_size': 999,  # Fake size to trigger check
      }
    ]

    # Max context 150 should allow 1 commit but not 2
    new_topics = cluster_topics.split_oversized_topics(
      original_topics, max_code_context=120
    )

    self.assertEqual(len(new_topics), 2)

    # First topic keeps ID 1
    self.assertEqual(new_topics[0]['topic_id'], 1)
    self.assertEqual(len(new_topics[0]['commits']), 1)

    # Second topic gets new ID (max existing + 1 = 2)
    self.assertEqual(new_topics[1]['topic_id'], 2)
    self.assertEqual(len(new_topics[1]['commits']), 1)

  def test_split_oversized_topics_no_split_needed(self):
    original_topics = [{'topic_id': 1, 'commits': [], 'commit_count': 0}]
    new_topics = cluster_topics.split_oversized_topics(
      original_topics, max_code_context=1000
    )
    self.assertEqual(len(new_topics), 1)
    self.assertEqual(new_topics, original_topics)

  # -------------------------------------------------------------------------
  # Integration / Main Test
  # -------------------------------------------------------------------------

  @patch('pathlib.Path.mkdir')  # ADDED MOCK: Prevents directory creation logic
  @patch('pathlib.Path.stat')  # ADDED MOCK: Handles file size check
  @patch('cluster_topics.load_embeddings')
  @patch('cluster_topics.load_prepared_commits')
  @patch('cluster_topics.perform_dimensionality_reduction')
  @patch('cluster_topics.perform_hdbscan_clustering')
  @patch('cluster_topics.assign_outliers_with_kmeans')
  @patch('cluster_topics.create_topics_from_labels')
  @patch('json.dump')
  def test_main_flow(
    self,
    mock_json,
    mock_create,
    mock_kmeans,
    mock_hdbscan,
    mock_umap,
    mock_load_commits,
    mock_load_emb,
    mock_stat,
    mock_mkdir,
  ):

    # Setup mocks
    mock_load_emb.return_value = (np.zeros((10, 10)), {})
    mock_load_commits.return_value = [
      {'id': i, 'commits': []} for i in range(10)
    ]
    mock_umap.return_value = np.zeros((10, 2))
    mock_hdbscan.return_value = np.zeros(10)
    mock_kmeans.return_value = np.zeros(10)
    mock_create.return_value = [
      {'topic_id': 1, 'commits': [], 'commit_count': 0}
    ]

    # Configure stat mock to return a valid size (needed for final print)
    mock_stat.return_value.st_size = 1024

    # Mock args
    test_args = [
      'script_name',
      'embeddings.npz',
      'commits.pkl',
      '--output-file',
      'out.json',
      '--no-split',
    ]

    with (
      patch('sys.argv', test_args),
      patch('pathlib.Path.exists', return_value=True),
      patch('builtins.open', mock_open()),
    ):
      cluster_topics.main()

      # Verify core steps were called
      mock_load_emb.assert_called()
      mock_load_commits.assert_called()
      mock_umap.assert_called()
      mock_hdbscan.assert_called()
      mock_create.assert_called()
      mock_json.assert_called()
      # Verify mkdir was called (and thus successfully mocked out)
      mock_mkdir.assert_called()
      # Verify stat was called (for file size check)
      mock_stat.assert_called()


if __name__ == '__main__':
  unittest.main()
