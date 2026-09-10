# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import sys
import os

from google import genai


def initialize_gemini_client() -> genai.Client:
  """
  Initializes and returns a Gemini API client, expected to be configured for Vertex AI.

  This function relies on the following environment variables being set
  in the execution environment (e.g., by the LUCI recipe):
  - GOOGLE_GENAI_USE_VERTEXAI: Must be set to 'true'.
  - GOOGLE_CLOUD_PROJECT: The GCP project ID (e.g., 'chrome-ci-vertex-provider').
  - GOOGLE_CLOUD_LOCATION: The GCP region (e.g., 'us-central1').

  Authentication will be handled by the Application Default Credentials
  of the service account running this script on the LUCI builder.
  """
  try:
    if not os.getenv('GOOGLE_GENAI_USE_VERTEXAI', '').lower() == 'true':
      print(
        "Error: GOOGLE_GENAI_USE_VERTEXAI environment variable is not set to 'true'. "
        "Vertex AI backend is required for service account authentication.",
        file=sys.stderr,
      )
      sys.exit(1)

    if not os.getenv('GOOGLE_CLOUD_PROJECT'):
      print(
        "Error: GOOGLE_CLOUD_PROJECT environment variable is not set.",
        file=sys.stderr,
      )
      sys.exit(1)

    if not os.getenv('GOOGLE_CLOUD_LOCATION'):
      print(
        "Error: GOOGLE_CLOUD_LOCATION environment variable is not set.",
        file=sys.stderr,
      )
      sys.exit(1)

    # When the environment variables are set, genai.Client() automatically
    # configures itself to use Vertex AI and service account credentials.
    client = genai.Client()
    print("Successfully initialized GenAI client for Vertex AI.")
    return client
  except Exception as e:
    print(
      "Error: Could not initialize the GenAI client for Vertex AI.",
      file=sys.stderr,
    )
    print(
      "Please ensure the environment variables GOOGLE_GENAI_USE_VERTEXAI, "
      "GOOGLE_CLOUD_PROJECT, and GOOGLE_CLOUD_LOCATION are correctly set.",
      file=sys.stderr,
    )
    print(
      "Also ensure the builder's service account has the necessary IAM permissions "
      "(e.g., Vertex AI User role) on the project.",
      file=sys.stderr,
    )
    print(f"Underlying error: {e}", file=sys.stderr)
    sys.exit(1)
