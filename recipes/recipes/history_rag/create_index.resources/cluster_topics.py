# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Performs clustering on commit embeddings to identify topics.

This script:
1. Loads embeddings from an NPZ file
2. Loads prepared commit documents
3. Reduces dimensionality using UMAP
4. Clusters using HDBSCAN + K-means for outliers
5. Splits oversized topics by code context
6. Saves clustering results to JSON

Output format:
    A JSON file containing:
    {
        "metadata": {
            "num_topics": int,
            "num_documents": int,
            "clustering_params": {...},
            ...
        },
        "topics": [
            {
                "topic_id": int,
                "document_indices": [int, ...],
                "commit_count": int,
                "commits": [...],
                "code_context_size": int
            },
            ...
        ]
    }
"""

import argparse
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

import hdbscan
import numpy as np
import umap
from sklearn.cluster import KMeans


def load_embeddings(embeddings_file: Path) -> tuple[np.ndarray, dict]:
  """
  Loads embeddings from an NPZ file.

  Returns:
      Tuple of (embeddings array, metadata dict)
  """
  print(f"Loading embeddings from {embeddings_file}...")
  try:
    data = np.load(embeddings_file, allow_pickle=True)
    embeddings = data['embeddings']
    metadata = data['metadata'][0] if 'metadata' in data else {}

    print(f"Loaded embeddings with shape: {embeddings.shape}")
    return embeddings, metadata
  except Exception as e:
    print(f"Error loading embeddings: {e}", file=sys.stderr)
    sys.exit(1)


def load_prepared_commits(input_file: Path) -> list[dict]:
  """
  Loads prepared commit documents from a pickle file.

  Returns:
      List of document dictionaries.
  """
  print(f"Loading prepared commits from {input_file}...")
  try:
    with open(input_file, 'rb') as f:
      documents = pickle.load(f)

    print(f"Loaded {len(documents)} documents.")
    return documents
  except (IOError, pickle.PickleError) as e:
    print(f"Error loading prepared commits: {e}", file=sys.stderr)
    sys.exit(1)


def perform_dimensionality_reduction(
  embeddings: np.ndarray,
  n_neighbors: int,
  n_components: int,
  metric: str = 'cosine',
) -> np.ndarray:
  """
  Reduces embedding dimensionality using UMAP.

  Args:
      embeddings: High-dimensional embeddings
      n_neighbors: Number of neighbors for UMAP
      n_components: Target dimensionality
      metric: Distance metric to use

  Returns:
      Reduced embeddings array
  """
  print("\nReducing dimensionality with UMAP...")
  print(
    f"  n_neighbors={n_neighbors}, n_components={n_components}, metric={metric}"
  )

  reducer = umap.UMAP(
    n_neighbors=n_neighbors,
    n_components=n_components,
    min_dist=0.0,
    metric=metric,
    random_state=42,
    verbose=True,
  )

  reduced_embeddings = reducer.fit_transform(embeddings)
  print(f"Reduced embeddings shape: {reduced_embeddings.shape}")

  return reduced_embeddings


def perform_hdbscan_clustering(
  reduced_embeddings: np.ndarray, min_cluster_size: int, min_samples: int
) -> np.ndarray:
  """
  Performs HDBSCAN clustering on reduced embeddings.

  Args:
      reduced_embeddings: Low-dimensional embeddings
      min_cluster_size: Minimum cluster size
      min_samples: Minimum samples for core points

  Returns:
      Array of cluster labels (-1 for outliers)
  """
  print("\nPerforming HDBSCAN clustering...")
  print(f"  min_cluster_size={min_cluster_size}, min_samples={min_samples}")

  clusterer = hdbscan.HDBSCAN(
    min_cluster_size=min_cluster_size,
    min_samples=min_samples,
    metric='euclidean',
    cluster_selection_method='eom',
  )

  clusterer.fit(reduced_embeddings)
  labels = clusterer.labels_

  n_clusters = len(np.unique(labels)) - (1 if -1 in labels else 0)
  n_outliers = np.sum(labels == -1)

  print(f"HDBSCAN found {n_clusters} clusters and {n_outliers} outliers.")

  return labels


def assign_outliers_with_kmeans(
  reduced_embeddings: np.ndarray, labels: np.ndarray
) -> np.ndarray:
  """
  Assigns outliers to nearest cluster using K-means.

  Args:
      reduced_embeddings: Low-dimensional embeddings
      labels: Initial cluster labels from HDBSCAN

  Returns:
      Updated labels with outliers assigned
  """
  outlier_indices = np.where(labels == -1)[0]

  if outlier_indices.size == 0:
    print("No outliers to assign.")
    return labels

  n_clusters = len(np.unique(labels)) - 1  # Exclude -1

  if n_clusters == 0:
    print(
      "Warning: No clusters found by HDBSCAN. Cannot assign outliers.",
      file=sys.stderr,
    )
    return labels

  print(
    f"\nAssigning {len(outlier_indices)} outliers to {n_clusters} clusters using K-means..."
  )

  # Calculate centroids from HDBSCAN's clustered points
  centroids = np.array(
    [reduced_embeddings[labels == i].mean(axis=0) for i in range(n_clusters)]
  )

  # Get embeddings for outliers
  outlier_embeddings = reduced_embeddings[outlier_indices]

  # Use K-means to assign outliers
  kmeans = KMeans(
    n_clusters=n_clusters, init=centroids, n_init=1, random_state=42
  )
  kmeans.fit(centroids)  # Fit on centroids to lock them
  outlier_labels = kmeans.predict(outlier_embeddings)

  # Update labels
  final_labels = np.copy(labels)
  final_labels[outlier_indices] = outlier_labels

  print("K-means assignment complete.")

  return final_labels


def format_code_context_from_aggregated_lines(aggregated_lines: dict) -> str:
  """Helper to format code context from a pre-aggregated dictionary."""
  output_parts = []
  for file_path in sorted(aggregated_lines.keys()):
    output_parts.append('\n' + file_path)
    sorted_lines = sorted(aggregated_lines[file_path].items())
    for line_num, line_content in sorted_lines:
      output_parts.append(f"{line_num}:{line_content}")

  return "\n".join(output_parts)


def format_code_context(commits: list[dict]) -> str:
  """
  Aggregates and formats the line data for all commits.

  Args:
      commits: List of commit dictionaries with 'files' field

  Returns:
      Formatted string with file paths and line numbers
  """
  aggregated_lines = {}
  for commit in commits:
    for file_path, lines in commit['files'].items():
      file_lines = aggregated_lines.setdefault(file_path, {})
      for line_num, line_content in lines:
        file_lines[line_num] = line_content

  return format_code_context_from_aggregated_lines(aggregated_lines)


def split_topic_by_code_context(
  commits: list[dict], max_code_context: int
) -> list[list[dict]]:
  """
  Splits a topic's commits into chunks based on code context size.

  Args:
      commits: List of commits in a topic (should be sorted by date)
      max_code_context: Maximum characters allowed per chunk

  Returns:
      List of commit chunks, each respecting the size limit
  """
  chunks = []
  current_chunk_commits = []
  aggregated_lines = {}

  for commit in commits:
    # Create tentative new aggregated lines
    new_aggregated_lines = {
      fp: lines.copy() for fp, lines in aggregated_lines.items()
    }

    # Add lines from new commit
    for file_path, lines in commit['files'].items():
      file_lines = new_aggregated_lines.setdefault(file_path, {})
      for line_num, line_content in lines:
        file_lines[line_num] = line_content

    # Calculate size of potential new context
    new_size = len(
      format_code_context_from_aggregated_lines(new_aggregated_lines)
    )

    if current_chunk_commits and new_size > max_code_context:
      # Current chunk is full, start a new one
      chunks.append(current_chunk_commits)

      # Start new chunk with current commit
      current_chunk_commits = [commit]
      aggregated_lines = {}
      for file_path, lines in commit['files'].items():
        file_lines = aggregated_lines.setdefault(file_path, {})
        for line_num, line_content in lines:
          file_lines[line_num] = line_content
    else:
      # Add to current chunk
      current_chunk_commits.append(commit)
      aggregated_lines = new_aggregated_lines

  if current_chunk_commits:
    chunks.append(current_chunk_commits)

  return chunks


def split_oversized_topics(
  topics: list[dict], max_code_context: int
) -> list[dict]:
  """
  Splits topics that exceed the maximum code context size.

  Args:
      topics: List of topic dictionaries
      max_code_context: Maximum characters allowed per topic

  Returns:
      New list of topics with oversized ones split
  """
  if max_code_context <= 0:
    return topics

  print(
    f"\nSplitting topics with code context larger than {max_code_context} characters..."
  )
  print("Checking topic sizes...")

  new_topics = []
  next_topic_id = max(t['topic_id'] for t in topics) + 1 if topics else 0
  split_count = 0

  for topic in topics:
    commits = topic['commits']
    code_context_size = len(format_code_context(commits))

    if code_context_size <= max_code_context:
      # Topic is fine, keep as is
      new_topics.append(topic)
      continue

    # Split this topic
    commits.sort(key=lambda c: c.get('date', ''))
    chunks = split_topic_by_code_context(commits, max_code_context)

    if len(chunks) <= 1:
      # Couldn't split effectively, keep as is
      new_topics.append(topic)
      continue

    split_count += 1

    # First chunk keeps original topic_id
    new_topics.append(
      {
        'topic_id': topic['topic_id'],
        'document_indices': topic['document_indices'],  # Keep for reference
        'commits': chunks[0],
        'commit_count': len(chunks[0]),
        'code_context_size': len(format_code_context(chunks[0])),
      }
    )

    # Subsequent chunks get new IDs
    for chunk in chunks[1:]:
      new_topics.append(
        {
          'topic_id': next_topic_id,
          'document_indices': [],  # These are split from original
          'commits': chunk,
          'commit_count': len(chunk),
          'code_context_size': len(format_code_context(chunk)),
        }
      )
      next_topic_id += 1

  print(f"Split {split_count} topics into {len(new_topics)} total topics.")

  return new_topics


def create_topics_from_labels(
  labels: np.ndarray, documents: list[dict]
) -> list[dict]:
  """
  Creates topic dictionaries from cluster labels and documents.

  Args:
      labels: Cluster assignment for each document
      documents: Original document dictionaries

  Returns:
      List of topic dictionaries
  """
  print("\nCreating topics from cluster labels...")

  # Group documents by topic
  topics_dict = {}
  for doc_idx, label in enumerate(labels):
    if label == -1:
      continue  # Skip outliers (shouldn't exist after K-means)

    if label not in topics_dict:
      topics_dict[label] = {
        'topic_id': int(label),
        'document_indices': [],
        'commits': [],
      }

    topics_dict[label]['document_indices'].append(int(doc_idx))
    topics_dict[label]['commits'].extend(documents[doc_idx]['commits'])

  # Convert to list and add stats
  topics = []
  for topic in topics_dict.values():
    # Sort commits by date
    topic['commits'].sort(key=lambda c: c.get('date', ''))
    topic['commit_count'] = len(topic['commits'])
    topic['code_context_size'] = len(format_code_context(topic['commits']))
    topics.append(topic)

  # Sort by commit count (descending)
  topics.sort(key=lambda t: t['commit_count'], reverse=True)

  print(f"Created {len(topics)} topics from {len(documents)} documents.")

  return topics


def main():
  """Main function to orchestrate clustering."""
  parser = argparse.ArgumentParser(
    description="Cluster commit embeddings to identify topics.",
    formatter_class=argparse.RawTextHelpFormatter,
  )
  parser.add_argument(
    "embeddings_file", type=str, help="Path to the embeddings NPZ file."
  )
  parser.add_argument(
    "commits_file", type=str, help="Path to the prepared commits pickle file."
  )
  parser.add_argument(
    "--output-file",
    type=str,
    required=True,
    help="Path to save the clustering results JSON file.",
  )
  parser.add_argument(
    "--min-cluster-size",
    type=int,
    default=None,
    help="Minimum number of documents for a cluster (default: 4).",
  )
  parser.add_argument(
    "--min-samples",
    type=int,
    default=4,
    help="Minimum samples for a core point in HDBSCAN (default: 4).",
  )
  parser.add_argument(
    "--n-components",
    type=int,
    default=10,
    help="Target dimensionality for UMAP (default: 10).",
  )
  parser.add_argument(
    "--umap-neighbors",
    type=int,
    default=10,
    help="Number of neighbors for UMAP (default: 10).",
  )
  parser.add_argument(
    "--max-code_context",
    type=int,
    default=300000,
    help="Maximum characters for topic code context. Topics exceeding this will be split (default: 300000).",
  )
  parser.add_argument(
    "--no-split",
    action="store_true",
    help="Disable splitting of oversized topics.",
  )
  parser.add_argument(
    "--save-reduced-embeddings",
    type=str,
    default=None,
    help="Optional: Save reduced embeddings to this NPZ file for visualization.",
  )

  args = parser.parse_args()

  # Load embeddings
  embeddings_file = Path(args.embeddings_file).resolve()
  if not embeddings_file.exists():
    print(
      f"Error: Embeddings file '{embeddings_file}' does not exist.",
      file=sys.stderr,
    )
    sys.exit(1)

  embeddings, embedding_metadata = load_embeddings(embeddings_file)

  # Load prepared commits
  commits_file = Path(args.commits_file).resolve()
  if not commits_file.exists():
    print(
      f"Error: Commits file '{commits_file}' does not exist.", file=sys.stderr
    )
    sys.exit(1)

  documents = load_prepared_commits(commits_file)

  # Validate dimensions match
  if len(embeddings) != len(documents):
    print(
      f"Error: Embedding count ({len(embeddings)}) doesn't match document count ({len(documents)}).",
      file=sys.stderr,
    )
    sys.exit(1)

  # Step 1: Dimensionality reduction
  reduced_embeddings = perform_dimensionality_reduction(
    embeddings, n_neighbors=args.umap_neighbors, n_components=args.n_components
  )

  # Step 2: HDBSCAN clustering
  num_docs = len(documents)
  if args.min_cluster_size is None:
    # Use sqrt logic, capped at 100
    args.min_cluster_size = max(3, min(int(np.sqrt(num_docs)), 100))

    # It is usually best practice to also scale min_samples
    # to be smaller than or equal to min_cluster_size
    args.min_samples = max(2, min(args.min_samples, args.min_cluster_size))

    print(
      f"Automatically set min_cluster_size to {args.min_cluster_size} based on {num_docs} documents."
    )
  else:
    print(f"Using user-provided min_cluster_size: {args.min_cluster_size}")
  labels = perform_hdbscan_clustering(
    reduced_embeddings,
    min_cluster_size=args.min_cluster_size,
    min_samples=args.min_samples,
  )

  # Step 3: Assign outliers with K-means
  final_labels = assign_outliers_with_kmeans(reduced_embeddings, labels)

  # Step 4: Create topics from labels
  topics = create_topics_from_labels(final_labels, documents)

  # Step 5: Split oversized topics
  if not args.no_split:
    topics = split_oversized_topics(topics, args.max_code_context)

  # Prepare output metadata
  metadata = {
    'num_topics': len(topics),
    'num_documents': len(documents),
    'total_commits': sum(len(doc['commits']) for doc in documents),
    'clustering_params': {
      'min_cluster_size': args.min_cluster_size,
      'min_samples': args.min_samples,
      'n_components': args.n_components,
      'umap_neighbors': args.umap_neighbors,
      'max_code_context': args.max_code_context if not args.no_split else None,
    },
    'embedding_metadata': embedding_metadata,
    'clustered_at': datetime.now().isoformat(),
  }

  # Save clustering results
  output_path = Path(args.output_file).resolve()
  output_path.parent.mkdir(parents=True, exist_ok=True)

  try:
    output_data = {'metadata': metadata, 'topics': topics}

    with open(output_path, 'w', encoding='utf-8') as f:
      json.dump(output_data, f, indent=2)

    print(f"\nSuccessfully saved clustering results to {output_path}")
    print(f"Total topics: {len(topics)}")
    print(f"File size: {output_path.stat().st_size / 1024 / 1024:.2f} MB")

    # Print topic size distribution
    commit_counts = [t['commit_count'] for t in topics]
    print("\nTopic size statistics:")
    print(f"  Min commits: {min(commit_counts)}")
    print(f"  Max commits: {max(commit_counts)}")
    print(f"  Mean commits: {np.mean(commit_counts):.1f}")
    print(f"  Median commits: {np.median(commit_counts):.1f}")

  except IOError as e:
    print(f"Error writing to output file {output_path}: {e}", file=sys.stderr)
    sys.exit(1)

  # Optionally save reduced embeddings
  if args.save_reduced_embeddings:
    reduced_path = Path(args.save_reduced_embeddings).resolve()
    try:
      np.savez_compressed(
        reduced_path,
        reduced_embeddings=reduced_embeddings,
        labels=final_labels,
        metadata=np.array(
          [
            {
              'n_components': args.n_components,
              'umap_neighbors': args.umap_neighbors,
              'reduced_at': datetime.now().isoformat(),
            }
          ],
          dtype=object,
        ),
      )
      print(f"\nSaved reduced embeddings to {reduced_path}")
    except IOError as e:
      print(f"Warning: Could not save reduced embeddings: {e}", file=sys.stderr)


if __name__ == "__main__":
  main()
