# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Groups topics into categories and packages results into a ZIP archive.

This script:
1. Loads summarized topics from JSON
2. Groups topics into high-level categories using LLM (optional)
3. Packages results into a ZIP file with:
   - index.pkl: Topic metadata with chunk embeddings
   - embeddings.npy: All chunk embeddings as numpy array
   - topics/topic_{id}.json: Detailed info for each topic

Output format:
    A ZIP file containing:
    - index.pkl: {"name": str, "topics": [...]}
    - embeddings.npy: numpy array of all embeddings
    - topics/topic_{id}.json: Individual topic details
"""

import argparse
import concurrent.futures
import hashlib
import io
import itertools
import json
import pickle
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

from google import genai
import numpy as np

import gemini_client


def load_summarized_topics(input_file: Path) -> dict:
  """
    Loads summarized topics from a JSON file.
    Returns:
        Dictionary with 'metadata' and 'topics' keys.
    """
  print(f"Loading summarized topics from {input_file}...")
  try:
    with open(input_file, 'r', encoding='utf-8') as f:
      data = json.load(f)

    print(f"Loaded {len(data['topics'])} summarized topics.")
    return data
  except (IOError, json.JSONDecodeError) as e:
    print(f"Error loading summarized topics: {e}", file=sys.stderr)
    sys.exit(1)


def call_llm_with_cache(prompt: str,
                        model_name: str,
                        client: genai.Client,
                        cache_dir: Path,
                        ignore_cache: bool = False) -> str | None:
  """
    Calls the Gemini API with caching.
    Args:
        prompt: The prompt to send
        model_name: Name of the Gemini model
        client: Initialized Gemini client
        cache_dir: Directory for caching responses
        ignore_cache: If True, bypass cache and force new API call
    Returns:
        Generated text or None if all retries fail
    """
  cache_key_input = prompt + model_name
  cache_hash = hashlib.sha256(cache_key_input.encode('utf-8')).hexdigest()
  cache_file = cache_dir / f"{cache_hash}.txt"

  if not ignore_cache and cache_file.exists():
    try:
      with open(cache_file, 'r', encoding='utf-8') as f:
        return f.read()
    except IOError:
      pass  # Fall through to API call

  for retry in range(10):
    try:
      response = client.models.generate_content(
          model=model_name, contents=[prompt])
      result = response.candidates[0].content.parts[0].text.strip()

      # Cache the result
      try:
        with open(cache_file, 'w', encoding='utf-8') as f:
          f.write(result)
      except IOError as e:
        print(f"Warning: Could not cache LLM response: {e}", file=sys.stderr)

      return result
    except Exception as e:
      if retry < 9:
        print(f"\nLLM API error: {e}")
        print(f"Retrying in 20s (attempt {retry + 2}/10)...")
        time.sleep(20)
      else:
        print(f"Error: LLM call failed after 10 attempts: {e}", file=sys.stderr)

  return None


def generate_group_categories(topics: list[dict], model_name: str,
                              client: genai.Client, cache_dir: Path,
                              num_groups: int) -> list[str] | None:
  """
    Automatically generates a specified number of topic groups using an LLM.
    Args:
        topics: List of topic dictionaries with 'title' field
        model_name: Name of the Gemini model
        client: Initialized Gemini client
        cache_dir: Directory for caching
        num_groups: Number of groups to generate
    Returns:
        List of group names, or None if failed
    """
  print(f"\nAutomatically generating {num_groups} topic groups using an LLM...")

  topics_to_categorize = [
      t for t in topics if t.get("title") != "Uncategorized"
  ]
  if not topics_to_categorize:
    print("No topics require LLM categorization.")
    return []

  all_titles_prompt_data = [
      f"Topic ID: {t['topic_id']}\nTitle: {t['title']}"
      for t in topics_to_categorize
  ]

  prompt = (
      "You are an expert software engineering manager. Your task is to analyze the following list of topic titles "
      f"and generate exactly {num_groups} high-level category names that summarize these topics.\n\n"
      "Follow these rules:\n"
      "1. Create concise, descriptive names for each category.\n"
      "2. Each category name must be in Title Case and be 8 words or less.\n"
      "3. Do NOT assign topics to the categories.\n\n"
      "Here are the topics to analyze:\n"
      "---------------------\n" + "\n\n".join(all_titles_prompt_data) +
      "\n---------------------\n\n"
      "Please provide your response as a plain text list, with one category name per line."
  )

  for attempt in range(10):
    ignore_cache = (attempt > 0)
    response_str = call_llm_with_cache(
        prompt, model_name, client, cache_dir, ignore_cache=ignore_cache)

    if not response_str:
      time.sleep(2)
      continue

    try:
      groups = [
          line.strip()
          for line in response_str.strip().split('\n')
          if line.strip()
      ]

      if len(groups) != num_groups:
        print(
            f"Warning: LLM generated {len(groups)} groups, expected {num_groups}. Retrying...",
            file=sys.stderr)
        continue

      print(f"Successfully generated {len(groups)} group categories:")
      for i, group in enumerate(groups, 1):
        print(f"  {i}. {group}")
      return groups

    except Exception as e:
      print(
          f"Warning: Could not parse group generation response (attempt {attempt + 1}): {e}",
          file=sys.stderr)
      time.sleep(2)

  print(
      "Error: Failed to generate groups after multiple retries.",
      file=sys.stderr)
  return None


def group_topics_with_fixed_categories(topics: list[dict], model_name: str,
                                       client: genai.Client, cache_dir: Path,
                                       workers: int,
                                       fixed_categories: list[str]) -> None:
  """
    Uses a generative LLM to group topics into a fixed set of major categories.

    Modifies topics in-place by adding 'group' field.

    Args:
        topics: List of topic dictionaries
        model_name: Name of the Gemini model
        client: Initialized Gemini client
        cache_dir: Directory for caching
        workers: Number of parallel workers
        fixed_categories: List of category names to use
    """
  print(
      f"\nGrouping topics into {len(fixed_categories)} categories using an LLM..."
  )

  topics_to_categorize = []
  for topic in topics:
    if topic.get("title") == "Uncategorized":
      topic["group"] = "Uncategorized"
    else:
      topics_to_categorize.append(topic)

  if not topics_to_categorize:
    print("No topics require LLM categorization.")
    return

  topic_id_map = {topic['topic_id']: topic for topic in topics_to_categorize}

  def process_batch(args):
    batch_of_topics, ignore_cache_for_run = args
    prompt_data = [
        f"Topic ID: {t['topic_id']}\nTitle: {t['title']}"
        for t in batch_of_topics
    ]

    prompt = (
        "You are an expert software engineering manager. Your task is to categorize the following topics, based on their titles, "
        "into one of the predefined categories provided below.\n\n"
        "Here are the fixed categories you MUST use:\n" +
        "\n".join(f"- {cat}" for cat in fixed_categories) +
        "\n\nHere are the topics to categorize:\n"
        "---------------------\n" + "\n\n".join(prompt_data) +
        "\n---------------------\n\n"
        "Please provide your response as a single JSON object. The keys of the object MUST be the exact category names "
        "from the list above. The value for each key should be an array of the integer Topic IDs that belong to that "
        "category. Every topic must be assigned to a category.")

    for attempt in range(3):
      ignore_cache = (attempt > 0) or ignore_cache_for_run
      response_str = call_llm_with_cache(
          prompt, model_name, client, cache_dir, ignore_cache=ignore_cache)

      if not response_str:
        time.sleep(2)
        continue

      try:
        # Strip markdown code blocks if present
        if response_str.startswith("```json"):
          response_str = response_str[7:-3].strip()
        elif response_str.startswith("```"):
          response_str = response_str[3:-3].strip()

        groups = json.loads(response_str)

        # Validate and assign groups
        for group_name, topic_ids in groups.items():
          if group_name not in fixed_categories:
            print(
                f"Warning: LLM returned unexpected category: '{group_name}'.",
                file=sys.stderr)
            continue
          for topic_id in topic_ids:
            if topic_id in topic_id_map:
              topic_id_map[topic_id]['group'] = group_name
        return  # Success

      except (json.JSONDecodeError, IndexError) as e:
        print(
            f"Warning: Could not parse JSON for batch (attempt {attempt + 1}): {e}",
            file=sys.stderr)
        time.sleep(2)

    print(
        "Error: Failed to process a batch after multiple retries.",
        file=sys.stderr)

  # Process in batches
  batch_size = 10
  topic_batches = [
      topics_to_categorize[i:i + batch_size]
      for i in range(0, len(topics_to_categorize), batch_size)
  ]

  print("Grouping topic batches...")
  with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
    # Force evaluation of the map with list() to ensure all threads complete
    list(
        executor.map(process_batch, zip(topic_batches,
                                        itertools.repeat(False))))

  # Retry failed categorizations without cache
  uncategorized_topics = [t for t in topics_to_categorize if 'group' not in t]
  if uncategorized_topics:
    print(
        f"\nRe-processing {len(uncategorized_topics)} topics that failed categorization (without cache)..."
    )
    retry_batches = [
        uncategorized_topics[i:i + batch_size]
        for i in range(0, len(uncategorized_topics), batch_size)
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
      list(
          executor.map(process_batch, zip(retry_batches,
                                          itertools.repeat(True))))

  # Set default for any remaining failures
  for topic in topics_to_categorize:
    if 'group' not in topic:
      topic['group'] = "Uncategorized"

  # Print grouping statistics
  group_counts = {}
  for topic in topics:
    group = topic.get('group', 'Uncategorized')
    group_counts[group] = group_counts.get(group, 0) + 1

  print("\nGrouping statistics:")
  for group, count in sorted(
      group_counts.items(), key=lambda x: x[1], reverse=True):
    print(f"  {group}: {count} topics")


def package_topics_to_zip(topics: list[dict], output_path: Path,
                          analysis_name: str, metadata: dict) -> None:
  """
    Packages topics into a ZIP file with index, embeddings, and detail files.
    Args:
        topics: List of enriched topic dictionaries
        output_path: Path to save the ZIP file
        analysis_name: Name for this analysis
        metadata: Metadata to include in the archive
    """
  print(f"\nPackaging {len(topics)} topics into ZIP file...")

  index_topics = []
  all_embeddings = []
  embedding_counter = 0

  try:
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
      for topic_data in topics:
        topic_id = topic_data["topic_id"]

        # Process chunks and embeddings
        chunks = []
        for i, chunk_content in enumerate(topic_data["summary_chunks"]):
          if i < len(topic_data["chunk_embeddings"]
                    ) and topic_data["chunk_embeddings"][i]:
            embedding = topic_data["chunk_embeddings"][i]
            all_embeddings.append(embedding)
            chunks.append({
                "chunk_id": i,
                "chunk_content": chunk_content,
                "embedding_index": embedding_counter
            })
            embedding_counter += 1

        if not chunks:
          print(
              f"Warning: Topic {topic_id} has no valid chunks, skipping.",
              file=sys.stderr)
          continue

        # Create index entry
        index_entry = {
            "topic_id": topic_id,
            "title": topic_data["title"],
            "group": topic_data.get("group", "Uncategorized"),
            "keywords": topic_data.get("keywords", []),
            "commit_count": topic_data["commit_count"],
            "code_context_lines": topic_data["code_context"].count('\n'),
            "chunks": chunks
        }
        index_topics.append(index_entry)

        # Create detail entry (without chunk embeddings to save space)
        detail_entry = {
            "topic_id": topic_id,
            "summary": topic_data["summary"],
            "code_context": topic_data["code_context"],
            "commits": topic_data["commits"]
        }
        zf.writestr(f'topics/topic_{topic_id}.json',
                    json.dumps(detail_entry, indent=2))

      # Write the index as a pickle file
      index_content = {
          "name": analysis_name,
          "topics": index_topics,
          "metadata": metadata
      }
      zf.writestr('index.pkl', pickle.dumps(index_content))

      # Write the embeddings as a numpy array file
      if all_embeddings:
        embeddings_array = np.array(all_embeddings, dtype=np.float32)
        with io.BytesIO() as npy_buffer:
          np.save(npy_buffer, embeddings_array, allow_pickle=False)
          zf.writestr('embeddings.npy', npy_buffer.getvalue())

        print(f"Packaged {len(all_embeddings)} chunk embeddings.")
      else:
        print("Warning: No embeddings to package.", file=sys.stderr)

    print(f"\nSuccessfully saved packed topic analysis to {output_path}")
    print(f"File size: {output_path.stat().st_size / 1024 / 1024:.2f} MB")

  except (IOError, zipfile.BadZipFile) as e:
    print(f"Error writing to output file {output_path}: {e}", file=sys.stderr)
    sys.exit(1)


def main():
  """Main function to orchestrate grouping and packaging."""
  parser = argparse.ArgumentParser(
      description="Group topics and package results into a ZIP archive.",
      formatter_class=argparse.RawTextHelpFormatter)
  parser.add_argument(
      "summarized_topics_file",
      type=str,
      help="Path to the summarized topics JSON file.")
  parser.add_argument(
      "--output-file",
      type=str,
      required=True,
      help="Path to save the final ZIP archive.")
  parser.add_argument(
      "--name",
      type=str,
      default="Topic Analysis",
      help="A name for this analysis (stored in the output index).")
  parser.add_argument(
      "--grouping-model",
      type=str,
      default="gemini-2.5-flash-lite-preview-09-2025",
      help="Name of the Gemini model for topic grouping.")
  parser.add_argument(
      "--cache-dir",
      type=str,
      default="./.grouping_cache",
      help="Directory to store cached grouping responses.")
  parser.add_argument(
      "-w",
      "--workers",
      type=int,
      default=10,
      help="Number of worker threads for grouping (default: 10).")
  parser.add_argument(
      "--group",
      type=str,
      action="append",
      dest="custom_groups",
      help="A group category to use for topic grouping. Can be repeated. Example: --group 'Performance' --group 'Security'"
  )
  parser.add_argument(
      "--gen-groups",
      type=int,
      default=None,
      help="Automatically generate this many group categories. Cannot be used with --group."
  )
  parser.add_argument(
      "--no-grouping",
      action="store_true",
      help="Skip grouping entirely (all topics assigned to 'Uncategorized').")

  args = parser.parse_args()

  # Validate grouping arguments
  if args.custom_groups and args.gen_groups:
    print(
        "Error: --group and --gen-groups cannot be used together.",
        file=sys.stderr)
    sys.exit(1)

  if args.no_grouping and (args.custom_groups or args.gen_groups):
    print(
        "Error: --no-grouping cannot be used with --group or --gen-groups.",
        file=sys.stderr)
    sys.exit(1)

  # Validate input file
  input_file = Path(args.summarized_topics_file).resolve()
  if not input_file.exists():
    print(f"Error: Input file '{input_file}' does not exist.", file=sys.stderr)
    sys.exit(1)

  # Load summarized topics
  topic_data = load_summarized_topics(input_file)
  topics = topic_data['topics']

  # Perform grouping if requested
  if args.no_grouping:
    print("\nSkipping grouping (--no-grouping specified).")
    for topic in topics:
      topic['group'] = "Uncategorized"
  elif args.custom_groups or args.gen_groups:
    # Setup cache and client
    cache_dir = Path(args.cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"Using cache directory: {cache_dir}")

    client = gemini_client.initialize_gemini_client()

    if args.custom_groups:
      # Use custom categories
      print(f"\nUsing {len(args.custom_groups)} custom group categories:")
      for i, group in enumerate(args.custom_groups, 1):
        print(f"  {i}. {group}")

      group_topics_with_fixed_categories(topics, args.grouping_model, client,
                                         cache_dir, args.workers,
                                         args.custom_groups)
    elif args.gen_groups:
      # Generate categories automatically
      generated_groups = generate_group_categories(topics, args.grouping_model,
                                                   client, cache_dir,
                                                   args.gen_groups)

      if generated_groups:
        group_topics_with_fixed_categories(topics, args.grouping_model, client,
                                           cache_dir, args.workers,
                                           generated_groups)
      else:
        print(
            "Warning: Could not generate group categories. Assigning all to 'Uncategorized'.",
            file=sys.stderr)
        for topic in topics:
          topic['group'] = "Uncategorized"
  else:
    print(
        "\nNo grouping strategy specified. Assigning all topics to 'Uncategorized'."
    )
    for topic in topics:
      topic['group'] = "Uncategorized"

  # Prepare final metadata
  final_metadata = {
      'analysis_name':
          args.name,
      'num_topics':
          len(topics),
      'grouping_strategy': ('custom' if args.custom_groups else
                            'auto-generated' if args.gen_groups else 'none'),
      'grouping_model':
          args.grouping_model if
          (args.custom_groups or args.gen_groups) else None,
      'source_metadata':
          topic_data.get('metadata', {}),
      'packaged_at':
          datetime.now().isoformat()
  }

  # Package into ZIP
  output_path = Path(args.output_file).resolve()
  output_path.parent.mkdir(parents=True, exist_ok=True)

  package_topics_to_zip(topics, output_path, args.name, final_metadata)


if __name__ == "__main__":
  main()
