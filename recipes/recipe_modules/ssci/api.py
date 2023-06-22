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

  def _get_product_version(self, chrome_version):
    """
    Extracts the product version from configuration. Since we're still in testing, this
    always returns something so we can ensure the build will continue.
    """
    if chrome_version is not None:
      return chrome_version

    commit_id = self.m.buildbucket.gitiles_commit.id[:6]
    return commit_id or "UNKNOWN"

  def run(
      self,
      src_dir,
      build_dir,
      spdx_out,
      target="//third_party/perfetto/src/tracing/ipc/producer:producer",
      chrome_version=None,
      json_artifact_out=None,
      json_library_out=None,
      third_party_out=None,
  ):

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

      if not json_artifact_out:
        json_artifact_out = self.m.path.mkdtemp().join("artifacts.json")
      if not json_library_out:
        json_library_out = self.m.path.mkdtemp().join("libraries.json")
      if not third_party_out:
        third_party_out = self.m.path.mkdtemp().join("third_party.json")

      self.m.step(
          'run depbot', [
              depbot_path, '--target', t, '--chromium-src-dir', src_dir,
              '--log-level', 'debug', '--gn-path',
              self.m.depot_tools.gn_py_path, '--build-dir', build_dir,
              '--json-artifact-output', json_artifact_out,
              '--json-library-output', json_library_out,
              '--fast=' + str(self.fast_mode)
          ],
          cost=self.m.step.ResourceCost(
              cpu=2 * self.m.step.CPU_CORE, memory=4000))

      self.m.step('upload artifacts to BigQuery', [
          bqupload_cipd_path, "--json-list=true", self.bq_art_table,
          json_artifact_out
      ])

      self.m.step('upload libraries to BigQuery', [
          bqupload_cipd_path, "--json-list=true", self.bq_lib_table,
          json_library_out
      ])

      # partybot uses gclient and relies on having depot_tools available in $PATH
      with self.m.depot_tools.on_path():
        # The vPython metadata files are found in the parent directory.
        with self.m.context(cwd=self.m.path.dirname(partybot_cipd_path)):
          self.m.step("run partybot to collect 3P deps", [
              "vpython3", "--vpython-spec=.vpython3", "-m", "partybot",
              self.m.path.dirname(src_dir), "--file", third_party_out, "--os",
              self.m.buildbucket.build.builder.builder
          ])

      self.m.step('upload third party dependencies to BigQuery', [
          bqupload_cipd_path, "--json-list=true", self.bq_thirdparty_table,
          third_party_out
      ])

      # Ensure the CIPD tool versions we're about to use in the SPDX
      # document actually mean something.
      depbot_cipd_version = self._cipd_version(
          "infra_internal/tools/security/depbot/${platform}",
          self.depbot_version)
      partybot_cipd_version = self._cipd_version(
          "infra_internal/tools/partybot", self.partybot_version)
      ssci_cipd_version = self._cipd_version("infra_internal/tools/ssci",
                                             self.ssci_version)

      # Determine whether SPDX file should be generated with minimal fields or not
      minimal_config = "-full-spdx"
      if self.minimal_spdx:
        minimal_config = "-minimal-spdx"

      # Combines the recipe name with the DepBot target as the product name.
      recipe_name = self.m.properties["recipe"].split("/")[-1]
      product = f'{recipe_name}.{t.replace("//", "")}'
      p_version = self._get_product_version(chrome_version)

      # The vPython metadata files are found in the parent directory.
      with self.m.context(cwd=self.m.path.dirname(ssci_cipd_path)):
        self.m.step(
            'run ssci tool to generate SPDX sbom', [
                "vpython3", "--vpython-spec=.vpython3", "-m", "ssci", "spdx",
                "-libraries", json_library_out, "-artifacts", json_artifact_out,
                "-thirdparty", third_party_out, "-depbot-version",
                depbot_cipd_version, "-partybot-version", partybot_cipd_version,
                "-ssci-version", ssci_cipd_version, "-output-file", spdx_out,
                "-chromium-src", src_dir, "-product", product,
                "-product-version", p_version, "-platform",
                self.m.platform.name, "-arch",
                f"{self.m.platform.arch}{self.m.platform.bits}", minimal_config
            ],
            step_test_data=(lambda: self.m.json.test_api.output(
                data=[{
                    "spdx": "yes"
                }], name="spdx")))
