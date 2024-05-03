# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Shareable implementation of the recipe side of Chromium's UTR.

TODO(crbug.com/41492686): Move as much code from
recipes/chromium/universal_test_runner.py into here as possible.
"""

from google.protobuf import json_format

from recipe_engine import recipe_api

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.recipe_engine import result as result_pb2
from PB.recipe_modules.build.chromium_utr.request import Request


class ChromiumUTRApi(recipe_api.RecipeApi):

  def create_rerun_result(self,
                          rerun_options: list[tuple[str, Request.RerunOptions]],
                          info: str,
                          output_properties_file: str) -> result_pb2.RawResult:
    """Create a result for retriggering the recipe

    Writes the provided rerun options and creates a RawResult meant for the CLI to
    retrigger the recipe. The presence of rerun_properties should indicate to the
    CLI that the recipe can be invoked again differently for a different result.
    The info will be included in the RawResult meant to provide additional
    information to the user (e.g. if gclient args from the builder are not set
    locally).

    Args:
        rerun_options: A list of strings and input properties. These strings
          will be presented to the user as options and the corresponding input
          properties fed back to the recipe when selected. An entry with empty
          input properties will signal the recipe should not be reinvoked for that
          prompt selection.
        info: Information string that the user should see
        output_properties_file: Where the rerun_options should be written
    Returns:
      A RawResult that should be returned to trigger a rerun
    """
    if output_properties_file:
      output = []
      for prompt, properties in rerun_options:
        output.append((prompt,
                       json_format.MessageToDict(
                           message=properties,
                           preserving_proto_field_name=True)))
      self.m.file.write_json(
          f'write output_properties_file {output_properties_file}',
          output_properties_file, output)
    return result_pb2.RawResult(
        status=common_pb2.FAILURE, summary_markdown=info)
