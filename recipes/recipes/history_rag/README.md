# History RAG Index Generation

This document describes the pipeline for generating the index for the History RAG (Retrieval-Augmented Generation) system. The pipeline is implemented as a LUCI recipe that processes a Git repository to extract commit history, group related commits into topics, and generate summaries and embeddings for these topics.

## Pipeline Overview

The index generation process is orchestrated by the `create_index.py` LUCI recipe. The pipeline consists of the following main stages:

1.  **Checkout Source Code:** Checks out the source code of the repository to be indexed.

2.  **Generate File Blame JSONs:** For each file in the repository, it generates a JSON file containing the commit hash responsible for each line. This process is incremental and uses a baseline from the previous run. This stage is handled by `git_data_processor.py blame` and `merge_blame_jsons.py`.

3.  **Generate Commit Hash JSONs:** The file blame data is inverted to create a mapping from each commit hash to the files and lines it affected. Then, for each commit, it fetches the commit message, author, and date, and stores this information in a separate JSON file. This is done by `git_data_processor.py collect` and `git_data_processor.py fetch`.

4.  **Generate Topics:** The individual commit data is then processed to identify and summarize topics. This stage is a pipeline of several scripts:
    *   `prepare_commits.py`: Groups commits by bug ID and prepares them for embedding.
    *   `generate_embeddings.py`: Generates embeddings for the prepared commit documents using a large language model (LLM).
    *   `cluster_topics.py`: Clusters the embeddings to group related commits into topics.
    *   `summarize_topics.py`: Generates a title and summary for each topic using an LLM.
    *   `group_and_package.py`: Groups topics into high-level categories and packages the final index into a ZIP archive.

5.  **Upload Index:** The final ZIP archive is uploaded to Google Cloud Storage.

## Script Details

This section provides details on each script in the pipeline, including its purpose, inputs, and outputs.

### `create_index.py`

This is the main LUCI recipe that orchestrates the entire index generation pipeline.

-   **What it does:** Manages the execution of all the steps in the pipeline, from checking out the source code to uploading the final index.
-   **Why it does it:** To provide a reliable and automated way to build the History RAG index. By encapsulating the logic in a LUCI recipe, the process can be scheduled, monitored, and executed consistently in a controlled environment.
-   **Inputs:** This script is a LUCI recipe and is triggered by the LUCI scheduler. It does not take command-line arguments in the traditional sense.
-   **Output:** A ZIP archive containing the generated index, uploaded to Google Cloud Storage.

### `create_index.resources/git_data_processor.py`

This script is a command-line tool with three sub-commands: `blame`, `collect`, and `fetch`.

#### `blame` command

-   **What it does:** Computes and caches git blame data for files in a directory.
-   **Why it does it:** To create the foundational data for the index. By identifying the commit hash for each line of code, we can later link code changes to the specific commits that introduced them.
-   **Inputs:**
    -   `directory`: The directory to process files from.
    -   `--output-dir`: The directory to save the blame index files.
    -   `--source-dir`: Path to the git repository directory.
    -   `--baseline-commit`: If provided, only process files that have changed since this commit.
-   **Output:** A set of JSON files, where each file corresponds to a source file and contains the blame information for each line.

    *Example output file (`<output-dir>/path/to/file.cc.json`):*
    ```json
    {
      "version": "0.1",
      "file_hash": "9d6fd29f55604702e62482488eb175a51e0a84d3",
      "lines": [
        "17e91cb8a3316c05775c566cabfa4b90c266f5f9",
        "...",
        "..."
      ]
    }
    ```

#### `collect` command

-   **What it does:** Inverts the blame index, creating a single JSON file that maps each commit hash to the files and lines it affected.
-   **Why it does it:** To shift the perspective from file-centric to commit-centric. This allows the pipeline to analyze the full scope of each commit as a single unit of change, which is more aligned with how developers think about their work.
-   **Inputs:**
    -   `directory`: The directory containing the blame index JSON files.
    -   `--source-dir`: The root directory of the source code.
    -   `--output-file`: The path to the output JSON file.
    -   `--context`: The number of lines of context to include around each blamed line.
-   **Output:** A single JSON file containing the aggregated blame data.

    *Example output file (`<output-file>`):*
    ```json
    {
      "a1b2c3d4e5f6...": {
        "repository": "v8",
        "files": {
          "src/foo.cc": [
            [12, "  if (some_condition) {"],
            [13, "    // line 13 context"]
          ]
        }
      },
      "...": {}
    }
    ```

#### `fetch` command

-   **What it does:** Fetches commit details (message, author, date) for each commit hash and creates a separate JSON file for each commit.
-   **Why it does it:** To enrich the commit data with human-readable context. The commit message is the primary source of information for understanding the intent of a change, and is crucial for the subsequent steps of summarization and topic modeling.
-   **Inputs:**
    -   `blame_details_file`: The JSON file from the `collect` command.
    -   `--output-dir`: The directory to save the commit detail JSON files.
    -   `--source-dir`: The root directory of the git repository.
-   **Output:** One JSON file per commit hash.

    *Example output file (`<output-dir>/a1b2c3d4e5f6....json`):*
    ```json
    {
      "message": "Commit message",
      "author": "author",
      "date": "date",
      "metadata": {},
      "version": "0.1",
      "repository": "v8",
      "files": { ... }
    }
    ```

### `create_index.resources/merge_blame_jsons.py`

-   **What it does:** Merges two directories of blame JSONs, with the second directory's files overwriting the first's.
-   **Why it does it:** To enable incremental updates of the blame data. It is much more efficient to only process the files that have changed since the last run and merge them into the existing baseline, rather than re-processing the entire repository every time.
-   **Inputs:**
    -   `dir_a`: The destination directory (baseline).
    -   `dir_b`: The source directory (fresh).
-   **Output:** The destination directory (`dir_a`) is updated with the contents of the source directory (`dir_b`).

### `create_index.resources/prepare_commits.py`

-   **What it does:** Prepares and pre-clusters commit data for topic analysis by grouping commits that share bug IDs.
-   **Why it does it:** To improve the quality of the topic modeling. Commits related to the same bug are highly likely to be part of the same logical change. By grouping them together beforehand, we provide a stronger signal to the clustering algorithm, leading to more coherent topics.
-   **Inputs:**
    -   `input_dir`: Directory containing the commit detail JSON files from `git_data_processor.py fetch`.
    -   `--output-file`: Path to save the prepared commits pickle file.
-   **Output:** A pickle file containing a list of documents, where each document is a group of one or more commits.

    *Example output format (conceptual):*
    ```python
    [
        {
            "message": "Concatenated messages for embedding",
            "cleaned_message": "Cleaned concatenated messages",
            "commits": [ # List of individual commits in this document
                {"hash": "...", "message": "...", ...},
            ]
        },
    ]
    ```

### `create_index.resources/generate_embeddings.py`

-   **What it does:** Generates high-dimensional vector embeddings for each prepared commit document using a Gemini model.
-   **Why it does it:** To convert the textual data of commit messages into a numerical format that machine learning models can process. Embeddings capture the semantic meaning of the text, allowing the clustering algorithm to group commits based on their content, not just keywords.
-   **Inputs:**
    -   `input_file`: Path to the prepared commits pickle file from `prepare_commits.py`.
    -   `--output-file`: Path to save the embeddings NPZ file.
-   **Output:** An NPZ file containing the embeddings.
    -   `embeddings`: A NumPy array of shape `(n_documents, embedding_dim)`.
    -   `doc_indices`: An array mapping each embedding to its document index.
    -   `metadata`: A dictionary with generation info.

### `create_index.resources/cluster_topics.py`

-   **What it does:** Clusters the commit embeddings to identify topics. It uses UMAP for dimensionality reduction and HDBSCAN for clustering.
-   **Why it does it:** To automatically discover and group related commits into thematic topics. This is the core of the topic modeling process, where the unstructured history of the repository is organized into a set of coherent narratives.
-   **Inputs:**
    -   `embeddings_file`: Path to the embeddings NPZ file from `generate_embeddings.py`.
    -   `commits_file`: Path to the prepared commits pickle file from `prepare_commits.py`.
    -   `--output-file`: Path to save the clustering results JSON file.
-   **Output:** A JSON file containing the identified topics.

    *Example output format:*
    ```json
    {
        "metadata": { ... },
        "topics": [
            {
                "topic_id": 0,
                "document_indices": [1, 5, 12],
                "commit_count": 5,
                "commits": [ ... ],
                "code_context_size": 1234
            },
        ]
    }
    ```

### `create_index.resources/summarize_topics.py`

-   **What it does:** Generates a title and summary for each topic using a Gemini model, and optionally extracts keywords using c-TF-IDF. It also chunks the summaries and generates embeddings for these chunks.
-   **Why it does it:** To make the machine-generated topics understandable to humans. The LLM provides a concise, natural language description of the common theme of the commits in a topic, making the vast history of the codebase digestible. Keyword extraction further enhances understanding by highlighting key terms, and chunking with embeddings prepares the summaries for efficient retrieval in the RAG system.
-   **Inputs:**
    -   `clusters_file`: Path to the clustering results JSON file from `cluster_topics.py`.
    -   `--output-file`: Path to save the summarized topics JSON file.
    -   `--top-n`: Number of top topics to summarize.
    -   `--skip-keywords`: If set, skips keyword extraction (resulting in faster processing, but no keywords in the output).
-   **Output:** A JSON file containing the summarized topics.

    *Example output format:*
    ```json
    {
        "metadata": { ... },
        "topics": [
            {
                "topic_id": 0,
                "title": "Example Topic Title",
                "summary": "This is a summary of the topic.",
                "keywords": ["keyword1", "keyword2"],
                "commit_count": 5,
                "code_context": "...",
                "commits": [ ... ],
                "summary_chunks": ["chunk1", "chunk2"],
                "chunk_embeddings": [[0.1, ...], [0.2, ...]]
            },
        ]
    }
    ```

### `create_index.resources/group_and_package.py`

-   **What it does:** Groups topics into high-level categories and packages all the generated data into a single ZIP archive.
-   **Why it does it:** To create a self-contained, portable, and easily consumable artifact for the RAG system. The high-level grouping provides an additional layer of organization, and the ZIP archive bundles all the necessary data for the RAG system to load and use the index.
-   **Inputs:**
    -   `summarized_topics_file`: Path to the summarized topics JSON file from `summarize_topics.py`.
    -   `--output-file`: Path to save the final ZIP archive.
    -   `--name`: A name for the analysis.
    -   `--gen-groups`: Automatically generate a specified number of group categories.
-   **Output:** A ZIP archive containing:
    -   `index.pkl`: Topic metadata with chunk embeddings.
    -   `embeddings.npy`: All chunk embeddings as a NumPy array.
    -   `topics/topic_{id}.json`: Detailed info for each topic.

