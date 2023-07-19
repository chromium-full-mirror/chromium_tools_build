# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json

from pathlib import Path
from recipe_engine import recipe_api

class SsciAPI(recipe_api.RecipeApi):

  def __init__(self, props, **kwargs):
    super().__init__(**kwargs)

    self.bq_art_table = props.bq_artifact_table or "ssci-dev.depbot.artifacts"
    self.bq_lib_table = props.bq_library_table or "ssci-dev.depbot.libraries"
    self.depbot_version = props.depbot_version or "latest"
    self.targets = props.targets
    self.partybot_version = props.partybot_version or "latest"
    self.bq_thirdparty_table = props.bq_thirdparty_table or "ssci-dev.depbot.third_party"
    self.ssci_version = props.ssci_version or "latest"
    # Proto3 defaults boolean fields to False
    self.minimal_spdx = props.minimal_spdx or False
    self.generated_sbom_artifacts = []

  def _cipd_version(self, package_name, package_version):
    """
    Checks if we're using a proper CIPD version tag, or 'latest'.
    If we're using latest, CIPD describe is used to resolve it to an InstanceID
    """
    if package_version != "latest":
      return package_version

    desc = self.m.cipd.describe(package_name, package_version)
    # Adds a v in front of the CIPD package versions to prevent issues where CIPD
    # package instance IDs can start with a `-` which can be interpreted as a CLI
    # flag.
    return f"v{desc.pin.instance_id}"

  def _get_product_version(self, chrome_version):
    """
    Extracts the product version from configuration. Since we're still in testing, this
    always returns something so we can ensure the build will continue.
    """
    if chrome_version is not None:
      return chrome_version

    # The gitiles_commit.id may return None if this build wasn't triggered by CI.
    # This case is handled by the SSCI tool which will fetch the git hash instead.
    return self.m.buildbucket.gitiles_commit.id[:6]

  def _target_specific_steps(self, target, src_dir, sbom_bucket, sbom_folder,
                             filename_postfix, bqupload_cipd_path,
                             ssci_cipd_path, depbot_cipd_version,
                             partybot_cipd_version, ssci_cipd_version,
                             chrome_version, execution_id, minimal_config,
                             third_party_out):
    library_file = target.get("libraries_file_path")
    artifact_file = target.get("artifacts_file_path")
    entry_point = target.get("entry_point")

    builder_column = 'builder="%s"' % self.m.buildbucket.builder_full_name
    entry_point_column = 'entry_point="%s"' % entry_point
    execution_id_column = 'execution_id="%s"' % execution_id
    target_column = 'target="%s"' % target.get("target")

    with self.m.step.nest('target specific steps for %s' % entry_point):
      self.m.step('upload %s artifacts to BigQuery' % entry_point, [
          bqupload_cipd_path, "-json-list=true", "-column", builder_column,
          "-column", execution_id_column, "-column", entry_point_column,
          "-column", target_column, self.bq_art_table, artifact_file
      ])

      self.m.step('upload %s libraries to BigQuery' % entry_point, [
          bqupload_cipd_path, "--json-list=true", "-column", builder_column,
          "-column", execution_id_column, "-column", entry_point_column,
          "-column", target_column, self.bq_lib_table, library_file
      ])

      # Combines the recipe name with the DepBot target as the product name.
      recipe_name = self.m.properties["recipe"].split("/")[-1]
      product = f'{recipe_name}.{execution_id}.{entry_point.replace("//", "")}'
      p_version = self._get_product_version(chrome_version)

      spdx_file = self.m.path.mkdtemp().join("spdx-out.json")
      spdx_out = self.m.json.output(name=product, leak_to=spdx_file)

      # The vPython metadata files are found in the parent directory.
      with self.m.context(cwd=self.m.path.dirname(ssci_cipd_path)):
        self.m.step(
            'run ssci tool to generate %s SPDX sbom' % entry_point, [
                "vpython3", "--vpython-spec=.vpython3", "-m", "ssci", "spdx",
                "-libraries", library_file, "-artifacts", artifact_file,
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

      if sbom_bucket and sbom_folder:
        filename = entry_point.replace("//", "") + ".json"
        if filename_postfix:
          filename = entry_point.replace("//", "") + filename_postfix + ".json"
        full_path = Path(sbom_folder, execution_id, filename).as_posix()
        self.m.gsutil.upload(
            spdx_file,
            sbom_bucket,
            full_path,
            name="upload %s SBOM " % filename)
        self.generated_sbom_artifacts.append(f'gs://{sbom_bucket}/{full_path}')

  def run(
      self,
      src_dir,
      build_dir,
      sbom_bucket=None,
      sbom_folder=None,
      sbom_filename_postfix=None,
      targets=None,
      chrome_version=None,
  ):

    # prefer targets supplied in properties.
    targets = self.targets or targets

    execution_id = f'luci-{self.m.buildbucket.build.id}'

    with self.m.step.nest('SSCI collection'):
      depbot_path = self.m.cipd.ensure_tool(
          'infra_internal/tools/security/depbot/${platform}',
          self.depbot_version)
      bqupload_cipd_path = self.m.cipd.ensure_tool(
          'infra/tools/bqupload/${platform}', 'latest')
      partybot_cipd_path = self.m.cipd.ensure_tool(
          'infra_internal/tools/partybot', self.partybot_version)
      ssci_cipd_path = self.m.cipd.ensure_tool('infra_internal/tools/ssci',
                                               self.ssci_version)

      # prepare outputs.
      depbot_json_output_dir = self.m.path.mkdtemp()
      depbot_json_summary_file = self.m.json.output(name="summary")
      third_party_out = self.m.path.mkdtemp().join("third_party.json")

      targetFlags = []

      for target in targets:
        targetFlags.extend(["--target", target])

      depbot_result = self.m.step(
          'run depbot',
          [
              depbot_path, '--chromium-src-dir', src_dir, '--log-level',
              'debug', '--gn-path', self.m.depot_tools.gn_py_path,
              '--build-dir', build_dir, '--json-output-dir',
              depbot_json_output_dir, '--json-summary-file',
              depbot_json_summary_file
          ] + targetFlags,
          cost=self.m.step.ResourceCost(
              cpu=2 * self.m.step.CPU_CORE, memory=4000),
          step_test_data=(lambda: self.m.json.test_api.output(
              name="summary",
              data={
                  "targets": [{
                      "entry_point": "//example:example",
                      "target": "//example:example",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }],
              })))

      depbot_execution_summary = depbot_result.json.outputs.get("summary")

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

      futures = []

      # Handle target specific steps.
      for target in depbot_execution_summary.get("targets"):
        futures.append(
            self.m.futures.spawn(self._target_specific_steps, target, src_dir,
                                 sbom_bucket, sbom_folder,
                                 sbom_filename_postfix, bqupload_cipd_path,
                                 ssci_cipd_path, depbot_cipd_version,
                                 partybot_cipd_version, ssci_cipd_version,
                                 chrome_version, execution_id, minimal_config,
                                 third_party_out))
      for fut in self.m.futures.iwait(futures):
        fut.result()

      # set generated in output properties
      info_step = self.m.step.empty("SBOM's generated")
      info_step.presentation.properties.update(
          {'ssci_generated_artifacts': sorted(self.generated_sbom_artifacts)})
