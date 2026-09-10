# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Helper functions to wrap libs API methods as CLI."""

from __future__ import annotations

import argparse
import inspect
import json

from typing import Callable, List, get_args, get_origin, get_type_hints

from libs.result_summary import (
  create_result_summary_from_output_json,
  BaseResultSummary,
)
from libs.test_binary import (
  create_test_binary_from_jsonish,
  BaseTestBinary,
  TaskRequest,
)
from libs.strategies import ReproducingStep


# type mapping for ArgumentParser
def type_TaskRequest(input_arg):
  with open(input_arg, 'r', encoding='utf-8') as fp:
    return TaskRequest.from_jsonish(json.load(fp))


def type_BaseTestBinary(input_arg):
  with open(input_arg, 'r', encoding='utf-8') as fp:
    return create_test_binary_from_jsonish(json.load(fp))


def type_BaseResultSummary(input_arg):
  with open(input_arg, 'r', encoding='utf-8') as fp:
    return create_result_summary_from_output_json(json.load(fp))


def type_ReproducingStep(input_arg):
  with open(input_arg, 'r', encoding='utf-8') as fp:
    return ReproducingStep.from_jsonish(json.load(fp))


type_mapping = {
  TaskRequest: type_TaskRequest,
  BaseTestBinary: type_BaseTestBinary,
  BaseResultSummary: type_BaseResultSummary,
  ReproducingStep: type_ReproducingStep,
}


def parse_args(args: list[str], methods: dict[str, Callable]):
  parser = argparse.ArgumentParser(
    description='Flaky reproducer libs API runner. This is to provide an'
    ' interface bridging recipe and the script running in swarming.'
  )
  subparsers = parser.add_subparsers(
    title='methods',
    description='Script libs API methods',
    dest='method',
    required=True,
  )

  for method, func in methods.items():
    subparser = subparsers.add_parser(method, help=func.__doc__)
    method_signature = inspect.signature(func)
    anns = get_type_hints(func)
    for parameter in method_signature.parameters.values():
      nargs = None
      argument_type = anns[parameter.name]
      if (
        get_origin(argument_type) == list
        and len(typ_args := get_args(argument_type)) == 1
      ):
        nargs = '*'
        argument_type = typ_args[0]
      type_func = type_mapping.get(argument_type, argument_type)
      subparser.add_argument('--' + parameter.name, nargs=nargs, type=type_func)

  return parser.parse_args(args)


def main(args, methods):
  """Entrypoint for script APIs for recipe."""
  args = parse_args(args, methods)

  assert args.method in methods, "Unknown method"
  args_vars = dict(vars(args))
  args_vars.pop('method')
  print(json.dumps(methods[args.method](**args_vars)))
