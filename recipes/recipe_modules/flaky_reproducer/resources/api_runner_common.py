# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Helper functions to wrap libs API methods as CLI."""

import argparse
import inspect
import json

from libs.result_summary import (create_result_summary_from_output_json,
                                 BaseResultSummary)
from libs.test_binary import (create_test_binary_from_jsonish, BaseTestBinary,
                              TaskRequest)
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


def parse_args(args, methods):
  parser = argparse.ArgumentParser(
      description='Flaky reproducer libs API runner. This is to provide an'
      ' interface bridging recipe and the script running in swarming.')
  subparsers = parser.add_subparsers(
      title='methods',
      description='Script libs API methods',
      dest='method',
      required=True)

  for method, func in methods.items():
    subparser = subparsers.add_parser(method, help=func.__doc__)
    method_signature = inspect.signature(func)
    for parameter in method_signature.parameters.values():
      nargs = None
      argument_type = parameter.annotation
      # Support type such as list[int]
      if (hasattr(parameter.annotation, '__origin__') and
          parameter.annotation.__origin__ == list and
          len(parameter.annotation.__args__) == 1):
        nargs = '*'
        argument_type = parameter.annotation.__args__[0]
      # type_mapping for libs
      argument_type = type_mapping.get(argument_type, argument_type)
      subparser.add_argument(
          '--' + parameter.name, nargs=nargs, type=argument_type)

  return parser.parse_args(args)


def main(args, methods):
  """Entrypoint for script APIs for recipe."""
  args = parse_args(args, methods)

  assert args.method in methods, "Unknown method"
  args_vars = dict(vars(args))
  args_vars.pop('method')
  print(json.dumps(methods[args.method](**args_vars)))
