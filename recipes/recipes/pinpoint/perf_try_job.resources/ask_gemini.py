#!/usr/bin/env python3
# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from google import genai

MODEL = "gemini-2.5-pro"


class GenAI:
  """Class for interacting with Gemini model."""

  def __init__(self):
    self.client = genai.Client(api_key='foo')

  def generate(self, prompt: str):
    response = self.client.models.generate_content(model=MODEL, contents=prompt)
    return response.text


def main():
  try:
    client = GenAI()
    print(client.generate('Hi Gemini!'))
  except Exception as e:
    print('Failed to initialize genai: %s', e)


if __name__ == '__main__':
  main()
