#!/usr/bin/env python3
# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
import argparse
import sys

from google import genai

MODEL = "gemini-2.5-pro"


def parse_args(args):
  """Parse arguments."""
  parser = argparse.ArgumentParser()
  parser.add_argument('--gemini_api_key', required=True, help='Gemini API Key.')
  options = parser.parse_args(args)
  return options


class GenAI:
  """Class for interacting with Gemini model."""

  def __init__(self, options):
    self.client = genai.Client(api_key=options.gemini_api_key)

  def generate(self, prompt: str):
    response = self.client.models.generate_content(model=MODEL, contents=prompt)
    return response.text


def main(argv):
  options = parse_args(argv)
  try:
    client = GenAI(options)
    print(client.generate('Hi Gemini!'))
  except Exception as e:
    print('Failed to initialize genai: %s', e)


if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
