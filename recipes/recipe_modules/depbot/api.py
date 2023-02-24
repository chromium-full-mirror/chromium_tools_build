# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json

from recipe_engine import recipe_api


class DepbotAPI(recipe_api.RecipeApi):

  def run(self, src_dir, build_dir, json_artifact_out, json_library_out):
    BQ_ART_TABLE_NAME = "ssci-dev.depbot.artifacts"
    BQ_LIB_TABLE_NAME = "ssci-dev.depbot.libraries"

    with self.m.step.nest('ssci collection'):
      depbot_path = self.m.cipd.ensure_tool(
          'infra_internal/tools/security/depbot/${platform}', 'latest')
      bqupload_cipd_path = self.m.cipd.ensure_tool(
          'infra/tools/bqupload/${platform}', 'latest')

      result = self.m.step(
          'run depbot', [
              depbot_path, '--target',
              '//third_party/perfetto/src/tracing/ipc/producer:producer',
              '--chromium-src-dir', src_dir, '--log-level', 'debug',
              '--gn-path', self.m.depot_tools.gn_py_path, '--build-dir',
              build_dir, '--json-artifact-output', json_artifact_out,
              '--json-library-output', json_library_out
          ],
          step_test_data=(lambda: self.m.json.test_api.output(
              data=[{
                  "field1": "1234"
              }, {
                  "field2": "1234"
              }], name='artifacts') + self.m.json.test_api.output(
                  data=[{
                      "field1": "1234"
                  }, {
                      "field2": "1234"
                  }],
                  name='libraries')))

      artifactRows = []
      for row in result.json.outputs.get('artifacts'):
        artifactRows.append(json.dumps(row))

      libraryRows = []
      for row in result.json.outputs.get('libraries'):
        libraryRows.append(json.dumps(row))

      self.m.step(
          'upload artifacts to BigQuery',
          [bqupload_cipd_path, BQ_ART_TABLE_NAME],
          stdin=self.m.raw_io.input(data='\n'.join(artifactRows)),
      )

      self.m.step(
          'upload libraries to BigQuery',
          [bqupload_cipd_path, BQ_LIB_TABLE_NAME],
          stdin=self.m.raw_io.input(data='\n'.join(libraryRows)),
      )
