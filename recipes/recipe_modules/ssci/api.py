# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import dataclasses
import os
import pathlib

from typing import Union

from recipe_engine import recipe_api


@dataclasses.dataclass
class CIPDPkg:
  version: str
  pkg_path: str
  tool_path: Union[str, None] = None


class SsciAPI(recipe_api.RecipeApi):

  def __init__(self, props, **kwargs):
    super().__init__(**kwargs)

    self.bq_art_table = props.bq_artifact_table or "ssci-dev.depbot.artifacts"
    self.bq_lib_table = props.bq_library_table or "ssci-dev.depbot.libraries"
    self.targets = props.targets
    self.bq_thirdparty_table = props.bq_thirdparty_table or "ssci-dev.depbot.third_party"
    # Proto3 defaults boolean fields to False
    self.minimal_spdx = props.minimal_spdx or False
    self.generated_sbom_artifacts = {}
    self.execution_id = ""

    # Used to store CIPD package information for each of the tools used
    self.bqupload = CIPDPkg(
        version="latest", pkg_path="infra/tools/bqupload/${platform}")
    self.depbot = CIPDPkg(
        version=props.depbot_version or "latest",
        pkg_path="infra_internal/tools/security/depbot/${platform}")
    self.partybot = CIPDPkg(
        version=props.partybot_version or "latest",
        pkg_path="infra_internal/tools/partybot")
    self.ssci_tool = CIPDPkg(
        version=props.ssci_version or "latest",
        pkg_path="infra_internal/tools/ssci")

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

  def _upload_to_bigquery(self, data_name, bq_args):
    """
    Runs the BigQuery upload and logs an error if it fails. If there's a failure in
    this step, we should still produce an SBOM.

    Args:
      data_name: name of what's being uploaded
      bq_args: list of args to provide to bqupload command
    """
    step_name = f"upload {data_name} to BigQuery"
    try:
      self.m.step(step_name,
                  [self.bqupload.tool_path, "-json-list=true"] + bq_args)
    except self.m.step.StepFailure:
      pass

  def _setup_ssci_tools(self):
    """
    Gets each of the CIPD tools needed to run the SSCI collection steps and verifies
    the package version.
    """
    for cipd_tool in [
        self.depbot, self.bqupload, self.partybot, self.ssci_tool
    ]:
      cipd_tool.tool_path = self.m.cipd.ensure_tool(cipd_tool.pkg_path,
                                                    cipd_tool.version)

      # We don't need to get this data for bqupload as its not used to create
      # the SBOM, only to upload collected data during the run.
      if cipd_tool is self.bqupload:
        continue

      # Checks if we're using a proper CIPD version tag, or 'latest'. If we're using
      # latest, CIPD describe is used to resolve it to an InstanceID. After the tag
      # is resolved the first time this becomes a no-op.
      if cipd_tool.version != "latest":
        continue

      desc = self.m.cipd.describe(cipd_tool.pkg_path, cipd_tool.version)
      # Adds a v in front of the CIPD package versions to prevent issues where CIPD
      # package instance IDs can start with a `-` which can be interpreted as a CLI
      # flag.
      cipd_tool.version = f"v{desc.pin.instance_id}"

  def _target_specific_steps(self, target, src_dir, sbom_bucket, sbom_folder,
                             filename_postfix, chrome_version, minimal_config,
                             third_party_out):
    library_file = target.get("libraries_file_path")
    artifact_file = target.get("artifacts_file_path")
    entry_point = target.get("entry_point")

    # These columns are added to collected depbot data before the data is
    # uploaded to BigQuery
    extra_depbot_columns = [
        "-column", f'builder="{self.m.buildbucket.builder_full_name}"',
        "-column", f'execution_id="{self.execution_id}"', "-column",
        f'entry_point="{entry_point}"', "-column",
        'target="{}"'.format(target.get("target"))
    ]

    with self.m.step.nest('target specific steps for %s' % entry_point):

      self._upload_to_bigquery(
          f"{entry_point} artifacts",
          extra_depbot_columns + [self.bq_art_table, artifact_file])

      self._upload_to_bigquery(
          f"{entry_point} libraries",
          extra_depbot_columns + [self.bq_lib_table, library_file])

      # Combines the recipe name with the DepBot target as the product name.
      recipe_name = self.m.properties["recipe"].split("/")[-1]
      product = f'{recipe_name}.{self.execution_id}.{entry_point.replace("//", "")}'
      p_version = self._get_product_version(chrome_version)

      spdx_file = self.m.path.mkdtemp().join("spdx-out.json")
      spdx_out = self.m.json.output(name=product, leak_to=spdx_file)

      # The vPython metadata files are found in the parent directory.
      with self.m.context(cwd=self.m.path.dirname(self.ssci_tool.tool_path)):
        self.m.step(
            f'run ssci tool to generate {entry_point} SPDX sbom', [
                "vpython3", "--vpython-spec=.vpython3", "-m", "ssci", "spdx",
                "-libraries", library_file, "-artifacts", artifact_file,
                "-thirdparty", third_party_out, "-depbot-version",
                self.depbot.version, "-partybot-version", self.partybot.version,
                "-ssci-version", self.ssci_tool.version, "-output-file",
                spdx_out, "-chromium-src", src_dir, "-product", product,
                "-product-version", p_version, "-platform",
                self.m.platform.name, "-arch",
                f"{self.m.platform.arch}{self.m.platform.bits}", minimal_config
            ],
            step_test_data=(lambda: self.m.json.test_api.output(
                data=[{
                    "spdx": "yes"
                }], name="spdx")))

      spdx_digest = self.m.file.file_hash(spdx_file, test_data='testhash')

      if sbom_bucket and sbom_folder:
        filename, target_ext = os.path.splitext(entry_point.replace("//", ""))
        filename = f"{filename}{filename_postfix or ''}{target_ext}.spdx.json"
        full_path = pathlib.Path(sbom_folder, self.execution_id,
                                 filename).as_posix()
        self.m.gsutil.upload(
            spdx_file, sbom_bucket, full_path, name=f"upload {filename} SBOM")
        self.generated_sbom_artifacts.update({
            entry_point: {
                'digest': spdx_digest,
                'file': f'gs://{sbom_bucket}/{full_path}'
            }
        })

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

    # ensure this dict is reset between calls to the module
    # in the same build, so the log step shows the actual SBOM's
    # generated not all of the SBOM's generated in the build.
    self.generated_sbom_artifacts = {}

    # prefer targets supplied in properties.
    targets = self.targets or targets

    with self.m.step.nest('SSCI collection'):
      self.execution_id = f"luci-{self.m.buildbucket.build.id}"
      self._setup_ssci_tools()

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
              self.depbot.tool_path, '--chromium-src-dir', src_dir,
              '--log-level', 'debug', '--gn-path',
              self.m.depot_tools.gn_py_path, '--build-dir', build_dir,
              '--json-output-dir', depbot_json_output_dir,
              '--json-summary-file', depbot_json_summary_file
          ] + targetFlags,
          cost=self.m.step.ResourceCost(
              cpu=2 * self.m.step.CPU_CORE, memory=4000),
          step_test_data=(lambda: self.m.json.test_api.output(
              name="summary",
              data={
                  "targets": [{
                      "entry_point": "Example.apk",
                      "target": "//example:example",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }],
              })))

      depbot_execution_summary = depbot_result.json.outputs.get("summary")

      # partybot uses gclient and relies on having depot_tools available in $PATH
      with self.m.depot_tools.on_path():
        # The vPython metadata files are found in the parent directory.
        with self.m.context(cwd=self.m.path.dirname(self.partybot.tool_path)):
          self.m.step("run partybot to collect 3P deps", [
              "vpython3", "--vpython-spec=.vpython3", "-m", "partybot",
              self.m.path.dirname(src_dir), "--file", third_party_out, "--os",
              self.m.buildbucket.build.builder.builder
          ])

      self._upload_to_bigquery('third party dependencies', [
          "-column", f'execution_id="{self.execution_id}"',
          self.bq_thirdparty_table, third_party_out
      ])

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
                                 sbom_filename_postfix, chrome_version,
                                 minimal_config, third_party_out))
      for fut in self.m.futures.iwait(futures):
        fut.result()

      # set generated in output properties
      info_step = self.m.step.empty("SBOM's generated")
      info_step.presentation.logs[
          'ssci_generated_artifacts'] = self.generated_sbom_artifacts
      info_step.presentation.properties.update({
          'ssci_generated_artifacts': [
              a['file'] for a in self.generated_sbom_artifacts.values()
          ]
      })
      return self.generated_sbom_artifacts
