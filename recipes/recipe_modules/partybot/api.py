# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
This module provides an API for running the third party monitoring tool against
any version of Chrome/ium source on disk. Metadata from any identified third
party dependencies will be extracted into the specified file.
"""

import json

from recipe_engine import recipe_api


class PartyBot(recipe_api.RecipeApi):
  """Recipe module for PartyBot, third party monitoring."""

  def _get_bot_os(self):
    """Get the operating system of the bot from the LUCI builder parameters."""
    bot_os = None
    for dim in self.m.buildbucket.build.infra.swarming.task_dimensions:
      if dim.key == "os":
        bot_os = dim.value

    if not bot_os:
      # Fall back to using something else instead, which we probably do have access
      # to. This is less ideal, but will keep the build alive in cases where this
      # field is less important like the Chromium recipe. This field is mostly used
      # to make it clear what data was collected from where and separate data.
      bot_os = self.m.buildbucket.build.builder.builder

    return bot_os

  def run(self, chrome_src, output, bqtable, owners=False, version="latest"):
    """
    Ensure the correct version of the third party monitor tool is downloaded.
    It is the callers responsibility to ensure the directory for the output
    file is writable.

    Args:
      chrome_src (api.path): Directory with Chrome/ium checkout
      output (api.json.output): Path to write out data to
      table (str): BigQuery table to upload to
      owners (bool): Whether to get OWNERS for deps from Gerrit during the run
      version (str): CIPD package version
    """
    # Ensure bqupload is fetched from CIPD for data upload
    bqupload = self.m.cipd.ensure_tool('infra/tools/bqupload/${platform}',
                                       'latest')
    partybot_path = self.m.cipd.ensure_tool('infra_internal/tools/partybot',
                                            version)
    bot_os = self._get_bot_os()

    # tpmon uses gclient and relies on having depot_tools available in $PATH
    with self.m.depot_tools.on_path():
      # The vPython metadata files are found in the parent directory.
      with self.m.context(cwd=self.m.path.dirname(partybot_path)):
        result = self.m.step(
            "run third party monitor", [
                "vpython3", "--vpython-spec=.vpython3", "-m", "partybot",
                chrome_src, "--file", output, "--os", bot_os, "--owners", owners
            ],
            step_test_data=(lambda: self.m.json.test_api.output(
                data=[{
                    "field1": "1234"
                }, {
                    "field2": "1234"
                }], name="deps")))

      dep_rows = []
      for row in result.json.outputs.get("deps"):
        dep_rows.append(json.dumps(row))

    self.m.step(
        'upload third party dependencies to BigQuery',
        [bqupload, bqtable],
        stdin=self.m.raw_io.input(data='\n'.join(dep_rows)),
    )
