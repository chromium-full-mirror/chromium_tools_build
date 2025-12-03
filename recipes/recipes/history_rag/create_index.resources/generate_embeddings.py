# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Generates embeddings for prepared commit documents using Google Gemini API.

This script:
1. Loads prepared commit documents from a pickle file
2. Generates high-dimensional embeddings using Google's embedding model
3. Caches embeddings to avoid redundant API calls
4. Saves embeddings with document mapping to an NPZ file

Output format:
    An NPZ file containing:
    - embeddings: numpy array of shape (n_documents, embedding_dim)
    - doc_indices: array mapping each embedding to its document index
    - metadata: dict with generation info (model, date, etc.)
"""

import argparse
import hashlib
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

from google import genai
import numpy as np
from google.genai import types


def load_prepared_commits(input_file: Path) -> list[dict]:
  """
    Loads prepared commit documents from a pickle file.
    Returns:
        List of document dictionaries with 'message', 'cleaned_message', 'commits'.
    """
  print(f"Loading prepared commits from {input_file}...")
  try:
    with open(input_file, 'rb') as f:
      documents = pickle.load(f)

    print(f"Loaded {len(documents)} documents.")
    total_commits = sum(len(doc['commits']) for doc in documents)
    print(f"Total commits represented: {total_commits}")

    return documents
  except (IOError, pickle.PickleError) as e:
    print(f"Error loading prepared commits: {e}", file=sys.stderr)
    sys.exit(1)


def generate_embeddings_batch(messages: list[str],
                              client: genai.Client,
                              batch_size: int = 100,
                              max_retries: int = 5) -> np.ndarray:
  """
    Generates embeddings for a list of text messages using the Gemini API.
    Args:
        messages: List of text strings to embed
        client: Initialized Gemini API client
        batch_size: Number of messages to send per API call
        max_retries: Maximum number of retry attempts per batch
    Returns:
        Numpy array of embeddings with shape (len(messages), embedding_dim)
    """
  embeddings = []
  total_batches = (len(messages) + batch_size - 1) // batch_size

  for i in range(0, len(messages), batch_size):
    current_batch_idx = i // batch_size + 1
    print(f"Processing batch {current_batch_idx}/{total_batches}...", end='\r')

    batch = messages[i:i + batch_size]

    # Truncate messages to API limit (2MB ~ 2,097,152 chars)
    batch = [m[:2097152] for m in batch]

    # Retry logic for API calls
    for retry in range(max_retries):
      try:
        result = client.models.embed_content(
            model="gemini-embedding-001",
            contents=batch,
            config=types.EmbedContentConfig(task_type="CLUSTERING"))
        embeddings.extend([e.values for e in result.embeddings])
        break
      except Exception as e:
        if retry < max_retries - 1:
          wait_time = 20 * (retry + 1)  # Exponential-ish backoff
          print(f"\nAPI error: {e}")
          print(
              f"Retrying batch in {wait_time}s (attempt {retry + 2}/{max_retries})..."
          )
          time.sleep(wait_time)
        else:
          print(
              f"\nFailed to generate embeddings after {max_retries} attempts.",
              file=sys.stderr)
          raise

  print("")  # Clear line after loop
  return np.array(embeddings)


def get_embeddings_with_cache(messages: list[str], cache_dir: Path,
                              client: genai.Client) -> np.ndarray:
  """
    Gets embeddings for messages, using a 1-to-1 cache for each message.

    Each message is hashed (SHA256) and cached individually. This allows
    for efficient reuse when processing overlapping datasets.

    Args:
        messages: List of text strings to embed
        cache_dir: Directory to store cached embeddings
        client: Initialized Gemini API client
    Returns:
        Numpy array of embeddings with shape (len(messages), embedding_dim)
    """
  embeddings_cache_dir = cache_dir / "embeddings"
  embeddings_cache_dir.mkdir(parents=True, exist_ok=True)

  all_embeddings = [None] * len(messages)
  messages_to_embed = []
  indices_to_embed = []

  print("Checking embedding cache...")
  # Check cache for each message
  for i, message in enumerate(messages):
    if i % 1000 == 0 and i > 0:
      print(f"Checked {i}/{len(messages)} messages...", end='\r')

    message_hash = hashlib.sha256(message.encode('utf-8')).hexdigest()
    cache_file = embeddings_cache_dir / f"{message_hash}.npy"

    if cache_file.exists():
      try:
        all_embeddings[i] = np.load(cache_file)
      except Exception as e:
        print(
            f"\nWarning: Could not load cached embedding {cache_file}: {e}",
            file=sys.stderr)
        messages_to_embed.append(message)
        indices_to_embed.append(i)
    else:
      messages_to_embed.append(message)
      indices_to_embed.append(i)

  print(
      f"Cache check complete. Found {len(messages) - len(messages_to_embed)} cached items."
  )

  # Generate embeddings for cache misses
  if messages_to_embed:
    print(f"Generating embeddings for {len(messages_to_embed)} new messages...")
    new_embeddings = generate_embeddings_batch(messages_to_embed, client)

    # Store new embeddings in cache and result array
    print("Saving new embeddings to cache...")
    for i, embedding in enumerate(new_embeddings):
      original_index = indices_to_embed[i]
      all_embeddings[original_index] = embedding

      # Save to cache
      message_to_cache = messages_to_embed[i]
      message_hash = hashlib.sha256(
          message_to_cache.encode('utf-8')).hexdigest()
      cache_file = embeddings_cache_dir / f"{message_hash}.npy"

      try:
        np.save(cache_file, embedding)
      except IOError as e:
        print(
            f"\nWarning: Could not cache embedding {cache_file}: {e}",
            file=sys.stderr)
  else:
    print("All embeddings found in cache!")

  return np.array(all_embeddings)


def initialize_gemini_client() -> genai.Client:
  """
    Initializes and returns a Gemini API client.
    Requires GOOGLE_API_KEY environment variable to be set.
    """
  try:
    client = genai.Client()
    print("Successfully initialized Gemini API client.")
    return client
  except Exception as e:
    print("Error: Could not initialize the GenAI client.", file=sys.stderr)
    print(
        "Please ensure GOOGLE_API_KEY environment variable is set.",
        file=sys.stderr)
    print(f"Underlying error: {e}", file=sys.stderr)
    sys.exit(1)


def main():
  """Main function to orchestrate embedding generation."""
  parser = argparse.ArgumentParser(
      description="Generate embeddings for prepared commit documents.",
      formatter_class=argparse.RawTextHelpFormatter)
  parser.add_argument(
      "input_file", type=str, help="Path to the prepared commits pickle file.")
  parser.add_argument(
      "--output-file",
      type=str,
      required=True,
      help="Path to save the embeddings NPZ file.")
  parser.add_argument(
      "--cache-dir",
      type=str,
      default="./.embedding_cache",
      help="Directory to store cached embeddings.")
  parser.add_argument(
      "--batch-size",
      type=int,
      default=100,
      help="Number of messages to send per API call (default: 100).")
  parser.add_argument(
      "--limit",
      type=int,
      default=None,
      help="Limit number of documents to embed (for testing).")

  args = parser.parse_args()

  # Validate input file
  input_file = Path(args.input_file).resolve()
  if not input_file.exists():
    print(f"Error: Input file '{input_file}' does not exist.", file=sys.stderr)
    sys.exit(1)

  # Setup cache directory
  cache_dir = Path(args.cache_dir).resolve()
  cache_dir.mkdir(parents=True, exist_ok=True)
  print(f"Using cache directory: {cache_dir}")

  # Initialize Gemini client
  client = initialize_gemini_client()

  # Load prepared commits
  documents = load_prepared_commits(input_file)

  # Apply limit if specified
  if args.limit:
    print(f"Limiting to first {args.limit} documents.")
    documents = documents[:args.limit]

  # Extract messages to embed
  # HARDCODED: Always use cleaned_message
  message_field = 'cleaned_message'
  try:
    messages = [doc[message_field] for doc in documents]
  except KeyError:
    print(
        f"Error: The key '{message_field}' was not found in one or more documents.",
        file=sys.stderr)
    print(
        "Ensure your input pickle file contains prepared documents with 'cleaned_message'.",
        file=sys.stderr)
    sys.exit(1)

  print(
      f"\nEmbedding {len(messages)} documents using field '{message_field}'...")

  # Generate embeddings with caching
  embeddings = get_embeddings_with_cache(messages, cache_dir, client)

  print(f"\nGenerated embeddings with shape: {embeddings.shape}")

  # Prepare output
  output_path = Path(args.output_file).resolve()
  output_path.parent.mkdir(parents=True, exist_ok=True)

  # Create metadata
  metadata = {
      'model': 'gemini-embedding-001',
      'task_type': 'CLUSTERING',
      'num_documents': len(documents),
      'embedding_dim': embeddings.shape[1],
      'generated_at': datetime.now().isoformat(),
      'message_field': message_field,
      'source_file': str(input_file)
  }

  try:
    # Save as NPZ file
    np.savez_compressed(
        output_path,
        embeddings=embeddings,
        doc_indices=np.arange(len(documents)),
        metadata=np.array([metadata], dtype=object)  # Wrap in array for NPZ
    )

    print(f"\nSuccessfully saved embeddings to {output_path}")
    print(f"Embedding shape: {embeddings.shape}")
    print(f"File size: {output_path.stat().st_size / 1024 / 1024:.2f} MB")

  except IOError as e:
    print(f"Error writing to output file {output_path}: {e}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
  main()
