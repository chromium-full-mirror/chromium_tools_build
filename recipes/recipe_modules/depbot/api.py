# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json

from recipe_engine import recipe_api

class DepbotAPI(recipe_api.RecipeApi):

  def __init__(self, props, **kwargs):
    super().__init__(**kwargs)

    self.bq_art_table = props.bq_artifact_table or "ssci-dev.depbot.artifacts"
    self.bq_lib_table = props.bq_library_table or "ssci-dev.depbot.libraries"
    self.depbot_version = props.depbot_version or "latest"
    self.fast_mode = props.fast_mode
    self.target = props.target or "//third_party/perfetto/src/tracing/ipc/producer:producer"

  def run(self, src_dir, build_dir, json_artifact_out, json_library_out):
    with self.m.step.nest('ssci collection'):
      depbot_path = self.m.cipd.ensure_tool(
          'infra_internal/tools/security/depbot/${platform}',
          self.depbot_version)
      bqupload_cipd_path = self.m.cipd.ensure_tool(
          'infra/tools/bqupload/${platform}', 'latest')

      result = self.m.step(
          'run depbot', [
              depbot_path, '--target', self.target, '--chromium-src-dir',
              src_dir, '--log-level', 'debug', '--gn-path',
              self.m.depot_tools.gn_py_path, '--build-dir', build_dir,
              '--json-artifact-output', json_artifact_out,
              '--json-library-output', json_library_out,
              '--fast=' + str(self.fast_mode)
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
          [bqupload_cipd_path, self.bq_art_table],
          stdin=self.m.raw_io.input(data='\n'.join(artifactRows)),
      )

      self.m.step(
          'upload libraries to BigQuery',
          [bqupload_cipd_path, self.bq_lib_table],
          stdin=self.m.raw_io.input(data='\n'.join(libraryRows)),
      )
