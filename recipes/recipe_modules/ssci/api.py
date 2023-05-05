# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json

from recipe_engine import recipe_api


class SsciAPI(recipe_api.RecipeApi):

  def __init__(self, props, **kwargs):
    super().__init__(**kwargs)

    self.bq_art_table = props.bq_artifact_table or "ssci-dev.depbot.artifacts"
    self.bq_lib_table = props.bq_library_table or "ssci-dev.depbot.libraries"
    self.depbot_version = props.depbot_version or "latest"
    self.fast_mode = props.fast_mode
    self.target = props.target or "//third_party/perfetto/src/tracing/ipc/producer:producer"
    self.partybot_version = props.partybot_version or "latest"
    self.bq_thirdparty_table = props.bq_thirdparty_table or "ssci-dev.depbot.third_party"
    self.ssci_version = props.ssci_version or "latest"

  def _cipd_version(self, package_name, package_version):
    """
    Checks if we're using a proper CIPD version tag, or 'latest'.
    If we're using latest, use CIPD describe to resolve it to a git hash
    """
    if package_version != "latest":
      return package_version

    desc = self.m.cipd.describe(package_name, package_version)
    for tag in desc.tags:
      if tag.tag.startswith('git_revision'):
        return tag.tag.split(':')[1]

  def run(self, src_dir, build_dir, json_artifact_out, json_library_out,
          third_party_out, spdx_out):
    with self.m.step.nest('ssci collection'):
      depbot_path = self.m.cipd.ensure_tool(
          'infra_internal/tools/security/depbot/${platform}',
          self.depbot_version)
      bqupload_cipd_path = self.m.cipd.ensure_tool(
          'infra/tools/bqupload/${platform}', 'latest')
      partybot_cipd_path = self.m.cipd.ensure_tool(
          'infra_internal/tools/partybot', self.partybot_version)
      ssci_cipd_path = self.m.cipd.ensure_tool('infra_internal/tools/ssci',
                                               self.ssci_version)

      depbot_result = self.m.step(
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
      for row in depbot_result.json.outputs.get('artifacts'):
        row["builder"] = self.m.buildbucket.builder_full_name
        artifactRows.append(json.dumps(row))

      libraryRows = []
      for row in depbot_result.json.outputs.get('libraries'):
        row["builder"] = self.m.buildbucket.builder_full_name
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

      # partybot uses gclient and relies on having depot_tools available in $PATH
      with self.m.depot_tools.on_path():
        # The vPython metadata files are found in the parent directory.
        with self.m.context(cwd=self.m.path.dirname(partybot_cipd_path)):
          partybot_result = self.m.step(
              "run partybot to collect 3P deps", [
                  "vpython3", "--vpython-spec=.vpython3", "-m", "partybot",
                  self.m.path.dirname(src_dir), "--file", third_party_out,
                  "--os", self.m.buildbucket.build.builder.builder
              ],
              step_test_data=(lambda: self.m.json.test_api.output(
                  data=[{
                      "field1": "1234"
                  }, {
                      "field2": "1234"
                  }],
                  name="third_party")))

      thirdparty_rows = []
      for row in partybot_result.json.outputs.get("third_party"):
        thirdparty_rows.append(json.dumps(row))

      self.m.step(
          'upload third party dependencies to BigQuery',
          [bqupload_cipd_path, self.bq_thirdparty_table],
          stdin=self.m.raw_io.input(data='\n'.join(thirdparty_rows)),
      )

      # Ensure the CIPD tool versions we're about to use in the SPDX
      # document actually mean something.
      depbot_cipd_version = self._cipd_version(
          "infra_internal/tools/security/depbot/${platform}",
          self.depbot_version)
      partybot_cipd_version = self._cipd_version(
          "infra_internal/tools/partybot", self.partybot_version)
      ssci_cipd_version = self._cipd_version("infra_internal/tools/ssci",
                                             self.ssci_version)

      data_dir = self.m.path.mkdtemp()
      self.m.file.write_json("write depbot libraries",
                             self.m.path.join(data_dir, "libs.json"),
                             depbot_result.json.outputs.get('libraries'))
      self.m.file.write_json("write depbot artifacts",
                             self.m.path.join(data_dir, "artifacts.json"),
                             depbot_result.json.outputs.get('libraries'))
      self.m.file.write_json("write third party data",
                             self.m.path.join(data_dir, "third_party.json"),
                             partybot_result.json.outputs.get('third_party'))

      # The vPython metadata files are found in the parent directory.
      with self.m.context(cwd=self.m.path.dirname(ssci_cipd_path)):
        self.m.step(
            'run ssci tool to generate SPDX sbom', [
                "vpython3", "--vpython-spec=.vpython3", "-m", "ssci", "spdx",
                "-libraries",
                self.m.path.join(data_dir, "libs.json"), "-artifacts",
                self.m.path.join(data_dir, "artifacts.json"), "-thirdparty",
                self.m.path.join(data_dir,
                                 "third_party.json"), "-depbot-version",
                depbot_cipd_version, "-partybot-version", partybot_cipd_version,
                "-ssci-version", ssci_cipd_version, "-output-file", spdx_out
            ],
            step_test_data=(lambda: self.m.json.test_api.output(
                data=[{
                    "spdx": "yes"
                }], name="spdx")))