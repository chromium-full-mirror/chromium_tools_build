# Copyright 2022 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Entrypoint to run the actual strategies on swarming bots."""

from __future__ import annotations

import argparse
import logging
import json
import os
import sys

from libs.test_binary import create_test_binary_from_jsonish
from libs.result_summary import create_result_summary_from_output_json
from libs.strategies import strategies


def parse_args(args):
  parser = argparse.ArgumentParser(
    description='Flaky reproducer strategy launcher.'
  )
  parser.add_argument(
    'strategy',
    metavar='strategy',
    choices=list(strategies.keys()),
    help="The name of strategy",
  )
  parser.add_argument(
    '--test-binary',
    required=True,
    # pylint: disable=unexpected-keyword-arg
    type=argparse.FileType('r', encoding='utf-8'),
    help="Filepath to test binary JSON file.",
  )
  parser.add_argument(
    '--result-summary',
    required=True,
    # pylint: disable=unexpected-keyword-arg
    type=argparse.FileType('r', encoding='utf-8'),
    help="Filepath to result summary file.",
  )
  parser.add_argument(
    '--output',
    required=False,
    # pylint: disable=unexpected-keyword-arg
    type=argparse.FileType('w', encoding='utf-8'),
    help=(
      "Write reproducing step as JSON into specified filename. Otherwise "
      "print to stdout."
    ),
  )
  parser.add_argument(
    'test_name', help="The name of failing test to be reproduced"
  )

  return parser.parse_args(args)


def main(args):
  """Entrypoint for the execution of a strategy on a swarming task."""
  args = parse_args(args)
  logging.getLogger().setLevel(logging.INFO)

  test_binary = create_test_binary_from_jsonish(json.load(args.test_binary))
  result_summary = create_result_summary_from_output_json(
    json.load(args.result_summary)
  )
  if args.test_name not in result_summary:
    raise LookupError(
      "Test not found in ResultSummary: {0}".format(args.test_name)
    )
  strategy = strategies[args.strategy](
    test_binary, result_summary, args.test_name
  )

  # Reset cwd to the root of CAS package.
  os.chdir(os.path.pardir)
  reproducing_step = strategy.run()
  print(
    'REPRODUCING_STEP.READABLE_INFO: {0}'.format(
      reproducing_step.readable_info()
    )
  )
  print(
    'REPRODUCING_STEP.DEBUG_INFO: {0}'.format(
      json.dumps(reproducing_step.debug_info, indent=2)
    )
  )
  if args.output:
    json.dump(reproducing_step.to_jsonish(), args.output)
  else:
    print(
      'REPRODUCING_STEP.JSON: {0}'.format(
        json.dumps(reproducing_step.to_jsonish())
      )
    )


if __name__ == '__main__':
  main(sys.argv[1:])  # pragma: no cover
