# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Generates LLM-based summaries and titles for the top N topics.

This script:
1. Loads clustering results from JSON
2. Selects the top N topics by commit count
3. Generates titles and summaries using Gemini LLM
4. Extracts keywords using c-TF-IDF (optional)
5. Chunks summaries and generates embeddings for each chunk
6. Saves summarized topics to JSON

Output format:
    A JSON file containing:
    {
        "metadata": {...},
        "topics": [
            {
                "topic_id": int,
                "title": str,
                "summary": str,
                "keywords": [str, ...],
                "commit_count": int,
                "code_context": str,
                "commits": [...],
                "summary_chunks": [str, ...],
                "chunk_embeddings": [[float, ...], ...]
            },
            ...
        ]
    }
"""

import argparse
import concurrent.futures
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from google import genai
import numpy as np
from google.genai import types
from sklearn.feature_extraction.text import CountVectorizer

import llm_prompts

import gemini_client

def load_clustering_results(input_file: Path) -> dict:
  """
    Loads clustering results from a JSON file.
    Returns:
        Dictionary with 'metadata' and 'topics' keys.
    """
  print(f"Loading clustering results from {input_file}...")
  try:
    with open(input_file, 'r', encoding='utf-8') as f:
      data = json.load(f)

    print(f"Loaded {len(data['topics'])} topics.")
    return data
  except (IOError, json.JSONDecodeError) as e:
    print(f"Error loading clustering results: {e}", file=sys.stderr)
    sys.exit(1)


def call_llm_with_cache(prompt: str,
                        model_name: str,
                        client: genai.Client,
                        cache_dir: Path,
                        ignore_cache: bool = False,
                        max_retries: int = 10,
                        temperature: float = 0.1) -> str | None:
  """
    Calls the Gemini API with caching.
    Args:
        prompt: The prompt to send
        model_name: Name of the Gemini model
        client: Initialized Gemini client
        cache_dir: Directory for caching responses
        ignore_cache: If True, bypass cache and force new API call
        max_retries: Maximum number of retry attempts
        temperature: The generation temperature.
    Returns:
        Generated text or None if all retries fail
    """
  cache_key_input = prompt + model_name + str(temperature)
  cache_hash = hashlib.sha256(cache_key_input.encode('utf-8')).hexdigest()
  cache_file = cache_dir / f"{cache_hash}.txt"

  if not ignore_cache and cache_file.exists():
    try:
      with open(cache_file, 'r', encoding='utf-8') as f:
        return f.read()
    except IOError:
      pass  # Fall through to API call

  for retry in range(max_retries):
    try:
      generation_config = types.GenerationConfig(temperature=temperature)
      response = client.models.generate_content(
          model=model_name,
          contents=[prompt],
          generation_config=generation_config)
      result = response.candidates[0].content.parts[0].text.strip()

      # Cache the result
      try:
        with open(cache_file, 'w', encoding='utf-8') as f:
          f.write(result)
      except IOError as e:
        print(f"Warning: Could not cache LLM response: {e}", file=sys.stderr)

      return result
    except Exception as e:
      if retry < max_retries - 1:
        wait_time = 20
        print(f"\nLLM API error: {e}")
        print(
            f"Retrying in {wait_time}s (attempt {retry + 2}/{max_retries})...")
        time.sleep(wait_time)
      else:
        print(
            f"Error: LLM call failed after {max_retries} attempts: {e}",
            file=sys.stderr)

  return None


def get_topic_summary_and_title(messages: list[str], model_name: str,
                                client: genai.Client, cache_dir: Path,
                                temperature: float) -> tuple[str, str]:
  """
    Uses a generative LLM to generate a title and summary for a topic.
    Args:
        messages: List of commit messages in the topic
        model_name: Name of the Gemini model
        client: Initialized Gemini client
        cache_dir: Directory for caching
        temperature: The generation temperature.
    Returns:
        Tuple of (title, summary)
    """
  prompt = llm_prompts.get_summary_and_title_prompt(messages)
  result_str = call_llm_with_cache(
      prompt, model_name, client, cache_dir, temperature=temperature)

  if not result_str:
    return "Untitled Topic", "Summary could not be generated."

  if '\n' not in result_str:
    return "Untitled Topic", result_str

  try:
    first_line, rest = result_str.split('\n', 1)
    title = first_line.strip().replace("*", "").replace("\"", "")
    summary = rest.strip()
    return title, summary
  except ValueError:
    return "Untitled Topic", result_str


def chunk_summary(summary: str, chunk_size: int = 3000) -> list[str]:
  """
    Splits a summary into chunks at newline boundaries.

    A chunk is created once it exceeds chunk_size and a newline is found.

    Args:
        summary: The summary text to chunk
        chunk_size: Target size for each chunk in characters

    Returns:
        List of summary chunks
    """
  if not summary:
    return []

  chunks = []
  while len(summary) > chunk_size:
    # Find the first newline after chunk_size
    split_pos = summary.find('\n', chunk_size)

    if split_pos != -1:
      chunks.append(summary[:split_pos])
      summary = summary[split_pos + 1:]  # Skip the newline
    else:
      # No newline found, rest is one chunk
      break

  if summary:
    chunks.append(summary)

  return chunks


def get_summary_embedding_with_cache(
    text: str, cache_dir: Path, client: genai.Client, model_name: str,
    output_dimensionality: int) -> list[float] | None:
  """
    Gets a retrieval document embedding for a single text, with caching.
    Args:
        text: Text to embed
        cache_dir: Directory for caching embeddings
        client: Initialized Gemini client
        model_name: Name of the embedding model to use
        output_dimensionality: Target dimension size (e.g., 768)
    Returns:
        Embedding as a list of floats, or None if failed
    """
  summary_embeddings_cache_dir = cache_dir / "summary_embeddings"
  summary_embeddings_cache_dir.mkdir(parents=True, exist_ok=True)

  cache_key_input = text + model_name + str(output_dimensionality)
  cache_key = hashlib.sha256(cache_key_input.encode('utf-8')).hexdigest()
  cache_file = summary_embeddings_cache_dir / f"{cache_key}.npy"

  if cache_file.exists():
    try:
      return np.load(cache_file).tolist()
    except Exception:
      pass  # Fall through to API call

  for retry in range(5):
    try:
      result = client.models.embed_content(
          model=model_name,
          contents=[text],
          config=types.EmbedContentConfig(
              task_type="RETRIEVAL_DOCUMENT",
              output_dimensionality=output_dimensionality))
      embedding = np.array(result.embeddings[0].values)

      # Cache the embedding
      try:
        np.save(cache_file, embedding)
      except IOError:
        pass  # Continue even if caching fails

      return embedding.tolist()
    except Exception as e:
      if retry < 4:
        print(
            f"Warning: Summary embedding failed, retrying (attempt {retry + 2}/5)...",
            file=sys.stderr)
        time.sleep(20)
      else:
        print(
            f"Error: Failed to generate summary embedding after 5 retries: {e}",
            file=sys.stderr)

  return None


def extract_topic_keywords(topics: list[dict]) -> dict[int, list[str]]:
  """
    Extracts keywords for each topic using the c-TF-IDF algorithm.

    Args:
        topics: List of processed topic dictionaries containing 'commits'

    Returns:
        Dictionary mapping topic_id to list of keywords
    """
  print("\nExtracting keywords using c-TF-IDF...")

  # Aggregate messages per topic directly from the topic structure
  topic_docs = {}
  for topic in topics:
    # distinct messages to avoid skewing TF-IDF with identical cherry-picks
    messages = [
        c['message'] for c in topic.get('commits', []) if 'message' in c
    ]
    topic_docs[topic['topic_id']] = " ".join(messages)

  if not topic_docs:
    print("Warning: No topics found to extract keywords from.", file=sys.stderr)
    return {}

  # Use c-TF-IDF to extract keywords
  # We need at least a few documents for IDF to make sense,
  # but if we have very few topics, we adjust min_df.
  min_doc_freq = 1 if len(topic_docs) <= 5 else 2

  try:
    vectorizer = CountVectorizer(
        stop_words="english", min_df=min_doc_freq, ngram_range=(1, 2))

    # values() is not guaranteed order in older python, so we enforce order via keys
    topic_ids = list(topic_docs.keys())
    corpus = [topic_docs[tid] for tid in topic_ids]

    X = vectorizer.fit_transform(corpus)
    words = vectorizer.get_feature_names_out()
    tf = X.toarray()

    # Calculate c-TF-IDF
    doc_freq = np.asarray((X > 0).sum(axis=0)).flatten()
    idf = np.log(len(topic_docs) / (1 + doc_freq))
    tfidf = tf * idf

    top_keywords = {}
    for i, topic_id in enumerate(topic_ids):
      # Get top 10 indices
      top_indices = tfidf[i].argsort()[-10:][::-1]
      top_keywords[topic_id] = [words[j] for j in top_indices]

    return top_keywords

  except ValueError as e:
    # Usually happens if vocabulary is empty
    print(
        f"Warning: Keyword extraction skipped (empty vocabulary?): {e}",
        file=sys.stderr)
    return {topic_id: [] for topic_id in topic_docs}
  except Exception as e:
    print(f"Warning: Keyword extraction failed: {e}", file=sys.stderr)
    return {topic_id: [] for topic_id in topic_docs}


def format_code_context(commits: list[dict]) -> str:
  """
    Aggregates and formats the line data for all commits in a topic.

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

  output_parts = []
  for file_path in sorted(aggregated_lines.keys()):
    output_parts.append('\n' + file_path)
    sorted_lines = sorted(aggregated_lines[file_path].items())
    for line_num, line_content in sorted_lines:
      output_parts.append(f"{line_num}:{line_content}")

  return "\n".join(output_parts)


def process_topic(topic_data: dict, model_name: str, embedding_model_name: str,
                  client: genai.Client, llm_cache_dir: Path,
                  embedding_cache_dir: Path, output_dimensionality: int,
                  temperature: float) -> dict:
  """
    Processes a single topic: generates title, summary, chunks, and embeddings.
    Args:
        topic_data: Topic dictionary from clustering results
        model_name: Name of the Gemini model for summarization
        embedding_model_name: Name of the Gemini model for embeddings
        client: Initialized Gemini client
        llm_cache_dir: Cache directory for LLM responses
        embedding_cache_dir: Cache directory for embeddings
        output_dimensionality: The target dimension size for embeddings
        temperature: The generation temperature.
    Returns:
        Enriched topic dictionary with title, summary, chunks, embeddings
    """
  topic_id = topic_data['topic_id']
  topic_commits = topic_data['commits']

  # Sort commits by date
  topic_commits.sort(key=lambda c: c.get('date', ''))

  # Format messages for LLM
  topic_messages = [
      f"--- Change on '{c['date']}', BUG ids: '{c['metadata'].get('Bug', []) + c['metadata'].get('BUG=', [])}' ---\n{c['message']}"
      for c in topic_commits
  ]

  # Generate title and summary
  title, summary = get_topic_summary_and_title(topic_messages, model_name,
                                               client, llm_cache_dir,
                                               temperature)

  # Format code context
  code_context = format_code_context(topic_commits)

  # Chunk summary and generate embeddings
  summary_chunks = chunk_summary(summary)
  chunk_embeddings = []
  for chunk in summary_chunks:
    embedding = get_summary_embedding_with_cache(chunk, embedding_cache_dir,
                                                 client, embedding_model_name,
                                                 output_dimensionality)
    chunk_embeddings.append(embedding)

  # Remove 'files' field from commits to reduce output size
  for commit in topic_commits:
    if 'files' in commit:
      del commit['files']

  return {
      "topic_id":
          int(topic_id),
      "title":
          title,
      "summary":
          summary,
      "keywords": [],  # Will be filled in later
      "commit_count":
          topic_data['commit_count'],
      "code_context":
          code_context,
      "commits":
          topic_commits,
      "summary_chunks":
          summary_chunks,
      "chunk_embeddings":
          chunk_embeddings,
      "code_context_size":
          topic_data.get('code_context_size', len(code_context))
  }


def main():
  """Main function to orchestrate topic summarization."""
  parser = argparse.ArgumentParser(
      description="Generate LLM summaries for the top N topics.",
      formatter_class=argparse.RawTextHelpFormatter)
  parser.add_argument(
      "clusters_file",
      type=str,
      help="Path to the clustering results JSON file.")
  parser.add_argument(
      "--output-file",
      type=str,
      required=True,
      help="Path to save the summarized topics JSON file.")
  parser.add_argument(
      "--top-n",
      type=int,
      default=100,
      help="Number of top topics to summarize (default: 100).")
  parser.add_argument(
      "--llm-model",
      type=str,
      default="gemini-2.5-flash-lite-preview-09-2025",
      help="Name of the Gemini model for summarization.")
  parser.add_argument(
      "--embedding-model",
      type=str,
      default="gemini-embedding-001",
      help="Name of the Gemini model for embeddings.")
  parser.add_argument(
      "--output-dimensionality",
      type=int,
      default=768,
      help="Dimension size of the output embeddings (default: 768).")
  parser.add_argument(
      "--cache-dir",
      type=str,
      default="./.summarization_cache",
      help="Directory to store cached LLM responses and embeddings.")
  parser.add_argument(
      "-w",
      "--workers",
      type=int,
      default=10,
      help="Number of worker threads for parallel processing (default: 10).")
  parser.add_argument(
      "--skip-keywords",
      action="store_true",
      help="Skip keyword extraction (faster, but no keywords in output).")
  parser.add_argument(
      '--temperature',
      type=float,
      default=0.1,
      help='Temperature for LLM generation (0.0-1.0). Lower is more deterministic.'
  )

  args = parser.parse_args()

  # Validate input file
  clusters_file = Path(args.clusters_file).resolve()
  if not clusters_file.exists():
    print(
        f"Error: Clusters file '{clusters_file}' does not exist.",
        file=sys.stderr)
    sys.exit(1)

  # Setup cache directories
  cache_dir = Path(args.cache_dir).resolve()
  llm_cache_dir = cache_dir / "llm_cache"
  embedding_cache_dir = cache_dir / "embedding_cache"
  llm_cache_dir.mkdir(parents=True, exist_ok=True)
  embedding_cache_dir.mkdir(parents=True, exist_ok=True)
  print(f"Using cache directory: {cache_dir}")

  # Initialize Gemini client
  client = gemini_client.initialize_gemini_client()

  # Load clustering results
  cluster_data = load_clustering_results(clusters_file)
  all_topics = cluster_data['topics']

  # Sort topics by commit count (should already be sorted, but be explicit)
  all_topics.sort(key=lambda t: t['commit_count'], reverse=True)

  # Select top N topics to summarize
  topics_to_summarize = all_topics[:args.top_n]
  print(
      f"\nSummarizing top {len(topics_to_summarize)} topics (out of {len(all_topics)} total)..."
  )
  print(f"Embedding dimensions: {args.output_dimensionality}")

  if len(topics_to_summarize) < len(all_topics):
    skipped_count = len(all_topics) - len(topics_to_summarize)
    skipped_commits = sum(t['commit_count'] for t in all_topics[args.top_n:])
    print(
        f"Skipping {skipped_count} smaller topics ({skipped_commits} commits).")

  # Process topics in parallel
  print(f"\nProcessing topics with {args.workers} workers...")

  def process_with_args(topic):
    return process_topic(topic, args.llm_model, args.embedding_model, client,
                         llm_cache_dir, embedding_cache_dir,
                         args.output_dimensionality, args.temperature)

  with concurrent.futures.ThreadPoolExecutor(
      max_workers=args.workers) as executor:
    # Replaced tqdm with simple list consumption
    summarized_topics = list(
        executor.map(process_with_args, topics_to_summarize))
    print("Summarization complete.")

  # Extract keywords if requested
  if not args.skip_keywords:
    # Updated call: pass summarized_topics directly
    keywords_per_topic = extract_topic_keywords(summarized_topics)

    for topic in summarized_topics:
      topic['keywords'] = keywords_per_topic.get(topic['topic_id'], [])

  # Prepare output metadata
  metadata = {
      'num_topics_total': len(all_topics),
      'num_topics_summarized': len(summarized_topics),
      'top_n': args.top_n,
      'llm_model': args.llm_model,
      'embedding_model': args.embedding_model,
      'embedding_dim': args.output_dimensionality,
      'clustering_metadata': cluster_data.get('metadata', {}),
      'summarized_at': datetime.now().isoformat()
  }

  # Save results
  output_path = Path(args.output_file).resolve()
  output_path.parent.mkdir(parents=True, exist_ok=True)

  try:
    output_data = {'metadata': metadata, 'topics': summarized_topics}

    with open(output_path, 'w', encoding='utf-8') as f:
      json.dump(output_data, f, indent=2)

    print(f"\nSuccessfully saved summarized topics to {output_path}")
    print(f"Summarized {len(summarized_topics)} topics")
    print(f"File size: {output_path.stat().st_size / 1024 / 1024:.2f} MB")

  except IOError as e:
    print(f"Error writing to output file {output_path}: {e}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
  main()
