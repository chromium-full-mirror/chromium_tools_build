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
    self.target = props.target
    self.partybot_version = props.partybot_version or "latest"
    self.bq_thirdparty_table = props.bq_thirdparty_table or "ssci-dev.depbot.third_party"
    self.ssci_version = props.ssci_version or "latest"
    # Proto3 defaults boolean fields to False
    self.minimal_spdx = props.minimal_spdx or False

  def _cipd_version(self, package_name, package_version):
    """
    Checks if we're using a proper CIPD version tag, or 'latest'.
    If we're using latest, CIPD describe is used to resolve it to an InstanceID
    """
    if package_version != "latest":
      return package_version

    desc = self.m.cipd.describe(package_name, package_version)
    return desc.pin.instance_id

  def _format_json_for_bq(self, json_rows, extra_columns=None):
    """Format JSON objects for writing to BQ

    Args:
      json_rows (list): list of JSON objects.
      extra_coumns (dict): key value pairs to add to each row.

    Returns:
      new line delimited JSON string.
    """
    rows = []
    for row in json_rows:
      if extra_columns:
        for k, v in extra_columns.items():
          row[k] = v
      rows.append(json.dumps(row))
      return '\n'.join(rows)

  def _get_product_version(self):
    """Extracts the product version from configuration."""
    # For now, just use the shortened chrome commit hash. This will need to be replaced
    # with extracting the actual version of the product/target we're running against
    # which differs by each recipe.
    return self.m.buildbucket.gitiles_commit.id[:6]

  def run(self,
          src_dir,
          build_dir,
          json_artifact_out,
          json_library_out,
          third_party_out,
          spdx_out,
          target="//third_party/perfetto/src/tracing/ipc/producer:producer"):


    # prefer target supplied in properties.
    t = self.target or target

    with self.m.step.nest('ssci collection for %s' % t):
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
              depbot_path, '--target', t, '--chromium-src-dir', src_dir,
              '--log-level', 'debug', '--gn-path',
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

      self.m.step(
          'upload artifacts to BigQuery',
          [bqupload_cipd_path, self.bq_art_table],
          stdin=self.m.raw_io.input(
              data=self._format_json_for_bq(
                  json_rows=depbot_result.json.outputs.get('artifacts'),
                  extra_columns={
                      "builder": self.m.buildbucket.builder_full_name
                  })),
      )

      self.m.step(
          'upload libraries to BigQuery',
          [bqupload_cipd_path, self.bq_lib_table],
          stdin=self.m.raw_io.input(
              data=self._format_json_for_bq(
                  json_rows=depbot_result.json.outputs.get('libraries'),
                  extra_columns={
                      "builder": self.m.buildbucket.builder_full_name
                  })),
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

      self.m.step(
          'upload third party dependencies to BigQuery',
          [bqupload_cipd_path, self.bq_thirdparty_table],
          stdin=self.m.raw_io.input(
              data=self._format_json_for_bq(
                  partybot_result.json.outputs.get("third_party"))),
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
                             depbot_result.json.outputs.get('artifacts'))
      self.m.file.write_json("write third party data",
                             self.m.path.join(data_dir, "third_party.json"),
                             partybot_result.json.outputs.get('third_party'))

      # Determine whether SPDX file should be generated with minimal fields or not
      minimal_config = "-full-spdx"
      if self.minimal_spdx:
        minimal_config = "-minimal-spdx"

      # Combines the recipe name with the DepBot target as the product name.
      recipe_name = self.m.properties["recipe"].split("/")[-1]
      product = f'{recipe_name}.{t.replace("//", "")}'
      p_version = self._get_product_version()

      # The vPython metadata files are found in the parent directory.
      with self.m.context(cwd=self.m.path.dirname(ssci_cipd_path)):
        self.m.step(
            'run ssci tool to generate SPDX sbom', [
                "vpython3", "--vpython-spec=.vpython3", "-m", "ssci", "spdx",
                "-libraries",
                self.m.path.join(data_dir, "libs.json"), "-artifacts",
                self.m.path.join(data_dir, "artifacts.json"), "-thirdparty",
                self.m.path.join(data_dir, "third_party.json"),
                "-depbot-version", depbot_cipd_version, "-partybot-version",
                partybot_cipd_version, "-ssci-version", ssci_cipd_version,
                "-output-file", spdx_out, "-chromium-src", src_dir, "-product",
                product, "-product-version", p_version, "-platform",
                self.m.platform.name, "-arch",
                f"{self.m.platform.arch}{self.m.platform.bits}", minimal_config
            ],
            step_test_data=(lambda: self.m.json.test_api.output(
                data=[{
                    "spdx": "yes"
                }], name="spdx")))
