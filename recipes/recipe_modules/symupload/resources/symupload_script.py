#!/usr/bin/env python3
# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""A lightweight wrapper script around the symupload command.

The symupload binary can be invoked directly, but our existing infrastructure
prevents us from invoking it without having the api key being exposed for
the V2 protocol.
"""

from __future__ import annotations

import argparse
from ast import dump
import hashlib
import os
import subprocess
import sys


def parse_arguments(input_args):
  """Parses known arguments

  Arguments:
    input_args: Array of arguments passed to this script

  Returns:
    Namespace object of arguments
  """
  parser = argparse.ArgumentParser()
  parser.add_argument(
    '--artifacts', help='Comma-delimited list of artifacts to symupload.'
  )
  parser.add_argument(
    '--artifact_type', help='Symbol upload type, one of [\'macho\' or \'dsym\']'
  )
  parser.add_argument(
    '--api-key-file',
    required=True,
    help='File containing the Symbol Server API key.',
  )
  parser.add_argument('--binary-path', help='Path to the symupload binary.')
  parser.add_argument(
    '--platform',
    help='Platform currently running on. Used to '
    'determine the binary (.exe for win)',
  )
  parser.add_argument(
    '--server-urls',
    help='Comma-delimited list of server urls to symupload the '
    'list of artifacts to.',
  )
  args = parser.parse_args(input_args)
  return args


def build_args(
  platform, artifact, artifact_type, server_url, api_key, dump_inline
):
  """
  Args:
    platform: (str) platform for args, one of [win, mac, linux]
    artifact: (str) path to the binary file for upload
    server_url: (str) url for upload
    api_key: api key for V2 symupload.
    dump_inline: if we should dump inline information on Windows.
  Returns:
    List of arguments for V2 protocol based on platform.
  """
  # This logic below ported from:
  # https://chrome-internal.googlesource.com/chrome/tools/release/scripts/+/
  # 7dde2e2b66e163680e6a30153096fc4de422e7e3/recipes/recipe_modules/chrome/
  # resources/official_utils.py#96
  cmd_args = []
  if platform.startswith('win'):
    # Windows symupload has completely different syntax than Mac and Linux :(
    # https://crrev.com/548ca6e382d41d1682a36361f67fc1cbc4e987e6/src/tools/
    # windows/symupload/symupload.cc#261
    # Specifically, "-p" is just a switch to activate v2 mode, and doesn't
    # take a <protocol> value, and the key is passed as the last positional
    # arg, not with the "-k" flag.
    if dump_inline:
      cmd_args.append('--i')
    # Set timeout to 3 mins.
    cmd_args.extend(
      ['--timeout', '180000', '-p', artifact, server_url, api_key]
    )
  else:
    # For types other than breakpad, tell symupload the type and basename.
    if artifact_type in {'macho', 'dsym'}:
      basename = os.path.basename(artifact)
      cmd_args.extend(['-t', artifact_type, '-c', basename])

    cmd_args.extend(
      ['-p', 'sym-upload-v2', '-k', api_key, artifact, server_url]
    )
  return cmd_args


def read_api_key(path_to_file):
  if not os.path.exists(path_to_file):
    return None

  with open(path_to_file, 'r') as fn:
    api_key = fn.read()

  # Don't put the actual API key in the logs, but include some sort of debug
  # logging that might be helpful if there are problems.
  print('API key size sanity check: %s' % len(api_key))
  sha = hashlib.sha256(api_key.encode())
  print('API key hash: %s' % sha.hexdigest())
  return api_key


def sanitize_args(cmd_args, api_key):
  # Don't use the default RunCommand command logging, which would print
  # the API key when using the v2 protocol.
  clean_args = ['********' if x == api_key else x for x in cmd_args]
  return clean_args


def upload_symbol_file(
  platform,
  artifact,
  artifact_type,
  url,
  api_key,
  symupload_binary_path,
  dump_inline,
):
  print('Uploading %s to %s' % (artifact, url))

  cmd_args = build_args(
    platform, artifact, artifact_type, url, api_key, dump_inline
  )
  print(
    '\n'
    + subprocess.list2cmdline(
      [symupload_binary_path] + sanitize_args(cmd_args, api_key)
    )
  )

  result = 0
  try:
    output = subprocess.check_output(
      [symupload_binary_path] + cmd_args, stderr=subprocess.STDOUT
    )
    print(output.decode('utf-8'))
  except subprocess.CalledProcessError as e:
    if e.returncode == 2:
      print('Skipping upload for existing symbol.')
    # Any other non-zero ret code returned as failure
    else:
      result = e.returncode
      print(
        'Failed to upload %s to %s. Return code %s with output %s'
        % (artifact, url, result, e.output)
      )
  return result


def main(args):
  args = parse_arguments(args)
  api_key = read_api_key(args.api_key_file)
  symupload_binary_path = args.binary_path
  dump_inline = False

  # Show the symupload help text, which could be useful to debug failures (e.g.
  # to see if the flags change).
  try:
    output = subprocess.check_output(
      [symupload_binary_path, '-h'], stderr=subprocess.STDOUT
    )
    output = output.decode('utf-8')
    dump_inline = "[--i]" in output
    print(output)
  except subprocess.CalledProcessError:
    pass

  artifacts = args.artifacts.split(',')
  server_urls = args.server_urls.split(',')

  result = 0
  for artifact in artifacts:
    for url in server_urls:
      return_code = upload_symbol_file(
        args.platform,
        artifact,
        args.artifact_type,
        url,
        api_key,
        symupload_binary_path,
        dump_inline,
      )
      if return_code != 0:
        result = return_code

  return result


if '__main__' == __name__:
  sys.exit(main(sys.argv[1:]))
