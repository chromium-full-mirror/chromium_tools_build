# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import contextlib
import dataclasses
import os
import pathlib

from recipe_engine import recipe_api
from recipe_engine.config_types import Path
from google.protobuf import json_format as jsonpb

from PB.recipe_modules.build.ssci.properties import GeneratedSBOM

# SBOM file extension.
SBOM_EXTENSION = '.spdx.json'

# Generic Archive name
GENERIC_ARCHIVE = 'generic_archive'


@dataclasses.dataclass
class CIPDPkg:
  ensure_version: str
  pkg_path: str
  tool_path: str | None = None
  resolved_version: str | None = None
  previous_ensure_version: str | None = None


class SsciAPI(recipe_api.RecipeApi):

  def __init__(self, props, **kwargs):
    super().__init__(**kwargs)

    self.bq_art_table = props.bq_artifact_table or "projects/ssci-dev/datasets/depbot/tables/artifacts"
    self.bq_lib_table = props.bq_library_table or "projects/ssci-dev/datasets/depbot/tables/libraries"
    self.targets = props.targets
    self.bq_thirdparty_table = props.bq_thirdparty_table or "ssci-dev.depbot.third_party"
    self.generated_sbom_artifacts = {}
    self.execution_id = ""
    self.build_platform = None
    self.enable_upload_collected_data = props.enable_upload_collected_data
    self.include_licenses = props.include_licenses

    # Used to store CIPD package information for each of the tools used
    self.bqupload = CIPDPkg(
        ensure_version="latest", pkg_path="infra/tools/bqupload/${platform}")
    self.depbot = CIPDPkg(
        ensure_version=props.depbot_version or "prod",
        pkg_path="infra_internal/tools/security/depbot/${platform}")
    self.partybot = CIPDPkg(
        ensure_version=props.partybot_version or "prod",
        pkg_path="infra_internal/tools/partybot")
    self.ssci_sbom = CIPDPkg(
        ensure_version=props.ssci_sbom_version or "prod",
        pkg_path="infra_internal/tools/security/ssci_sbom/${platform}")
    self.ssci_uploader = CIPDPkg(
        ensure_version=props.ssci_uploader_version or "latest",
        pkg_path="infra_internal/tools/security/ssci_uploader/${platform}")

    self.ssci_tools = [
        self.depbot, self.bqupload, self.partybot, self.ssci_sbom,
        self.ssci_uploader
    ]

  @contextlib.contextmanager
  def custom_tool_versions(self, versions):
    """
    Temporarily overrides the versions of specified SSCI tools.

    Args:
        versions: A dict of (package_path: version) specifying the desired versions.
    """

    tool_lookup = {t.pkg_path: t for t in self.ssci_tools}

    try:
      # Use provided versions.
      for pkg_path, version in versions.items():
        tool = tool_lookup.get(pkg_path)
        if tool:
          tool.previous_ensure_version = tool.ensure_version
          tool.ensure_version = version
      yield
    finally:
      # Reset the tool versions.
      for pkg_path, version in versions.items():
        tool = tool_lookup.get(pkg_path)
        if tool:
          tool.ensure_version = tool.previous_ensure_version
          tool.previous_ensure_version = None

  def _get_product_version(self, chrome_version=None):
    """
    Extracts the product version from configuration. Since we're still in testing, this
    always returns something so we can ensure the build will continue.
    """
    if chrome_version is not None:
      return chrome_version

    # The gitiles_commit.id may return None if this build wasn't triggered by CI.
    # This case is handled by the SSCI tool which will fetch the git hash instead.
    return self.m.buildbucket.gitiles_commit.id[:6]

  def _upload_collected_data(
      self,
      data_name,
      bq_args,
      file_to_upload,
      gcs_file_name,
      sbom_folder,
      sbom_bucket="chrome-sbom",
      # TODO: b/326007424 - Deprecate and move all calls
      # to the new more performant API.
      use_bq_write_api=False):
    """
    Uploads collected data to both BigQuery and GCS. The BigQuery upload can be flaky
    so a failure in this step should not cause the SBOM generation to fail. The data
    collected during SBOM generation from DepBot and PartyBot should always be
    uploading to the 'chrome-sbom' bucket regardless of where the final SBOM gets
    uploaded. This data is used for debugging and data analysis and should not pollute
    any signed or unsigned buckets.

    Args:
      data_name: name of what's being uploaded
      bq_args: list of args to provide to bqupload command
      file_to_upload: Path to the file to upload
      gcs_file_name: What to call the uploaded GCS file
      sbom_folder: A folder prefix to use when uploading
      sbom_bucket: Bucket that should be used for data upload
      use_bq_write_api: Use the new BigQuery write API.
    """

    # The time taken to upload artifacts can vary dramatically
    # and slow builds down, it's best for now to enable the flag
    # as needed in the builder properties.
    if not self.enable_upload_collected_data:
      return

    step_name = f"upload {data_name} to BigQuery"
    # TODO(dlf): crbug/324078360 - poor streaming insert performance.
    bq_step_timeout = 60
    try:
      bq_tool = [self.bqupload.tool_path, "-json-list=true"]
      if use_bq_write_api:
        bq_tool = [self.ssci_uploader.tool_path, "-json_array=true"]

      self.m.step(
          step_name,
          bq_tool + bq_args + [file_to_upload],
          infra_step=True,
          timeout=bq_step_timeout)
    except self.m.step.StepFailure:
      pass

    if sbom_bucket and sbom_folder:
      cloud_file_path = pathlib.Path(sbom_folder, self.execution_id,
                                     gcs_file_name).as_posix()
      self.m.gsutil.upload(
          file_to_upload,
          sbom_bucket,
          cloud_file_path,
          name=f"upload {data_name} to GCS")

  def _handle_renaming(self, entry_point, to_rename):
    """
    Some artifacts are renamed during builds and we need to pass this
    rename through to the SBOM generation step so that the SPDX document
    name aligns with the final build names. Without doing this, the SBOMs
    are unlikely to be correctly surfaced during a search in SCILo.

    If provided, to_rename should be a key, value pair which specifies
    how artifacts should be renamed. eg.
      {"SystemWebViewGoogle": "AndroidWebview"}
    turns `SystemWebViewGoogle.apk` into `AndroidWebview.apk`.
    """
    artifact_name = entry_point.replace("//", "")
    filename, _ = os.path.splitext(artifact_name)

    if to_rename and filename in to_rename:
      artifact_name = artifact_name.replace(filename, to_rename[filename])
    return artifact_name

  def _make_filename_from_target(self,
                                 artifact_name,
                                 artifact_postfix,
                                 file_extension="json"):
    """
    Creates a filename which makes sense for artifacts and files by combining
    the target entry point name with the build channel and file name suffix.

    Args:
      artifact_name: Used to specify the base filename, or entry point.
      artifact_postfix:
        A postfix to add directly after the filename. This is typically used
        to specify the build channel.
      file_extension: File extension to use when naming the file

    Returns: A full file name to use when uploading data
    """
    filename, artifact_ext = os.path.splitext(artifact_name)
    if file_extension:
      file_extension = f".{file_extension}"

    return f"{filename}{artifact_postfix or ''}{artifact_ext}{file_extension}"

  def _setup_ssci_tools(self, tools):
    """
    Gets each of the CIPD tools needed to run the SSCI collection steps and verifies
    the package version.
    """
    for cipd_tool in tools:
      cipd_tool.tool_path = self.m.cipd.ensure_tool(cipd_tool.pkg_path,
                                                    cipd_tool.ensure_version)

      # This becomes a no-op after the tag is resolved the first time.
      if cipd_tool.resolved_version:
        continue

      # Checks if we're using a proper CIPD version tag, or 'latest'. If we're using
      # latest, CIPD describe is used to resolve it to an InstanceID.
      desc = self.m.cipd.describe(cipd_tool.pkg_path, cipd_tool.ensure_version)
      # This also adds a v in front of the CIPD package versions to prevent issues where
      # CIPD package instance IDs can start with a `-` which can be interpreted as a CLI
      # flag.
      cipd_tool.resolved_version = f"v{desc.pin.instance_id}"

  def _batch_targets(self, targets, batch_size=15):
    """Some recipes have many targets, and processing them all at once can cause builders to run out of memory and crash."""
    for i in range(0, len(targets), batch_size):
      yield targets[i:i + batch_size]

  def _target_specific_steps(self, target, src_dir, sbom_bucket, sbom_folder,
                             filename_postfix, chrome_version, third_party_out,
                             to_rename):

    library_file = target.get("libraries_file_path")
    artifact_file = target.get("artifacts_file_path")
    entry_point = target.get("entry_point")
    entry_point_name = self._handle_renaming(entry_point, to_rename)

    # These columns are added to collected depbot data before the data is
    # uploaded to BigQuery
    extra_depbot_columns = [
        "-column", f'builder="{self.m.buildbucket.builder_full_name}"',
        "-column", f'execution_id="{self.execution_id}"', "-column",
        f'entry_point="{entry_point}"', "-column",
        'target="{}"'.format(target.get("target"))
    ]

    # If the original entry point name was modified by the renaming step,
    # show both for context so it's clear what happened and what artifacts
    # relate to what.
    display_name = entry_point
    if entry_point not in entry_point_name:
      display_name = f"{entry_point_name} ({entry_point})"

    with self.m.step.nest('target specific steps for %s' % display_name):

      if sbom_bucket and sbom_folder:
        for data_name, bq_table, proto_name, data_file in [
            ("artifacts", self.bq_art_table, "ArtifactBigQueryRow",
             artifact_file),
            ("libraries", self.bq_lib_table, "LibraryBigQueryRow", library_file)
        ]:
          # Renames the files so they align with their generated SBOMs.
          # eg. SystemWebViewStable.apk.libraries.json
          filename = self._make_filename_from_target(
              entry_point_name, f"{filename_postfix or ''}.{data_name}")
          self._upload_collected_data(
              data_name,
              extra_depbot_columns + [bq_table, proto_name],
              data_file,
              filename,
              sbom_folder,
              use_bq_write_api=True)

      # Combines the recipe name with the DepBot target as the product name.
      final_artifact_name = self._make_filename_from_target(
          entry_point_name, filename_postfix, file_extension="")
      recipe_name = self.m.properties["recipe"].split("/")[-1]
      product = f'{recipe_name}.{self.execution_id}.{final_artifact_name}'
      p_version = self._get_product_version(chrome_version)

      # Get the directory that contains the src directory.
      parent_dir, _ = self.m.path.split(src_dir)

      # Generate the SBOM using the SSCI SBOM Generator.
      spdx_file = self.m.path.mkdtemp().joinpath("spdx-out.json")
      command = [
          self.ssci_sbom.tool_path,
          "-output-file",
          spdx_file,
          "-sbom-generator-version",
          self.ssci_sbom.resolved_version,
          "-product",
          product,
          "-product-version",
          p_version,
          "-platform",
          self.build_platform,
          "-partybot-version",
          self.partybot.resolved_version,
          "-third-party-file",
          third_party_out,
          "-chromium-path",
          parent_dir,
          "-depbot-version",
          self.depbot.resolved_version,
          "-artifacts-file",
          artifact_file,
          "-libraries-file",
          library_file,
      ]
      if self.include_licenses:
        command.append("-add-licenses")
      self.m.step(
          f'run SSCI SBOM Generator for {display_name} SBOM',
          command,
          infra_step=True,
          step_test_data=(lambda: self.m.json.test_api.output(
              data=[{
                  "spdx": "yes"
              }], name="ssci_sbom_spdx")))

      spdx_digest = self.m.file.file_hash(spdx_file, test_data='testhash')
      filename = self._make_filename_from_target(
          entry_point_name, filename_postfix, file_extension="spdx.json")

      generated_sbom = GeneratedSBOM(
          digest=spdx_digest,
          filename=final_artifact_name,
          sbom_name=filename,
          sbom_path=f'{spdx_file}',
          target=entry_point,
      )

      self.generated_sbom_artifacts.update(
          {final_artifact_name: generated_sbom})

      if sbom_bucket and sbom_folder:
        full_path = pathlib.Path(sbom_folder, self.execution_id,
                                 filename).as_posix()
        self.m.gsutil.upload(
            spdx_file, sbom_bucket, full_path, name=f"upload {filename} SBOM")

        self.generated_sbom_artifacts[
            final_artifact_name].file = f'gs://{sbom_bucket}/{full_path}'

      return final_artifact_name

  def run(
      self,
      src_dir,
      build_dir,
      sbom_bucket=None,
      sbom_folder=None,
      sbom_filename_postfix=None,
      targets=None,
      chrome_version=None,
      platform=None,
      to_rename=None,
      archive_names=None,
      skip_targets=False,
  ):

    # ensure this dict is reset between calls to the module
    # in the same build, so the log step shows the actual SBOM's
    # generated not all of the SBOM's generated in the build.
    self.generated_sbom_artifacts = {}

    # prefer targets supplied in properties.
    targets = self.targets or targets

    if platform is None:
      platform = f"{self.m.platform.name}_{self.m.platform.arch}{self.m.platform.bits}"
    self.build_platform = platform

    with self.m.step.nest('SSCI collection'):
      self.execution_id = f"luci-{self.m.buildbucket.build.id}"
      self._setup_ssci_tools(tools=self.ssci_tools)

      # prepare outputs.
      depbot_json_output_dir = self.m.path.mkdtemp()
      depbot_json_summary_file = self.m.json.output(name="summary")

      partybot_output_dir = self.m.path.cleanup_dir / 'partybot'
      self.m.file.ensure_directory('ensure Partybot output directory',
                                   partybot_output_dir)
      third_party_out = partybot_output_dir.joinpath(
          f'third_party_{self.execution_id}.json')

      targetFlags = []

      for target in targets:
        targetFlags.extend(["--target", target])

      extra_flags = []
      if archive_names:
        extra_flags.extend(["--archive-name", GENERIC_ARCHIVE])

      depbot_result = self.m.step(
          'run depbot',
          [
              self.depbot.tool_path, '--chromium-src-dir', src_dir,
              '--log-level', 'debug', '--gn-path',
              self.m.depot_tools.gn_py_path, '--build-dir', build_dir,
              '--json-output-dir', depbot_json_output_dir,
              '--json-summary-file', depbot_json_summary_file
          ] + targetFlags + extra_flags,
          infra_step=True,
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

      # Only run Partybot if it hasn't been run for this build before.
      if not self.m.path.exists(third_party_out):
        # Partybot uses gclient and relies on having depot_tools available in $PATH
        with self.m.depot_tools.on_path():
          # The vPython metadata files are found in the parent directory.
          with self.m.context(cwd=self.m.path.dirname(self.partybot.tool_path)):
            self.m.step(
                "run partybot to collect 3P deps", [
                    "vpython3",
                    "--vpython-spec=.vpython3",
                    "-m",
                    "partybot",
                    self.m.path.dirname(src_dir),
                    "--file",
                    third_party_out,
                    "--os",
                    self.m.buildbucket.build.builder.builder,
                    "--readme-licenses-only",
                ],
                infra_step=True)

        filename = self._make_filename_from_target("ThirdPartyData",
                                                   sbom_filename_postfix)
        self._upload_collected_data('third party dependencies', [
            "-column", f'execution_id="{self.execution_id}"',
            self.bq_thirdparty_table
        ], third_party_out, filename, sbom_folder)
      else:
        skip_partybot = self.m.step.empty('using existing Partybot results')
        skip_partybot.presentation.step_text = \
          f'Partybot output: {self.m.path.basename(third_party_out)}'

      futures = []
      targets_from_depbot = depbot_execution_summary.get("targets")
      if not skip_targets and targets_from_depbot is not None:
        # Be sure to batch targets to prevent overloading the
        # builder.
        for batch in self._batch_targets(targets_from_depbot):
          # Handle target specific steps.
          for target in batch:
            futures.append(
                self.m.futures.spawn(self._target_specific_steps, target,
                                     src_dir, sbom_bucket, sbom_folder,
                                     sbom_filename_postfix, chrome_version,
                                     third_party_out, to_rename))
          for fut in self.m.futures.iwait(futures):
            fut.result()

      # If an archive summary is present build an SBOM for each of the
      # given artifacts.
      archive_summary = depbot_execution_summary.get("archive")
      if archive_summary is not None and archive_summary.get(
          "name") == GENERIC_ARCHIVE:
        for a in archive_names:
          target = {
              "entry_point": a,
              "target": ",".join(archive_summary.get("targets")),
              "artifacts_file_path": archive_summary.get("artifacts_file_path"),
              "libraries_file_path": archive_summary.get("libraries_file_path")
          }
          final = self._target_specific_steps(target, src_dir, sbom_bucket,
                                              sbom_folder,
                                              sbom_filename_postfix,
                                              chrome_version, third_party_out,
                                              to_rename)

          self.generated_sbom_artifacts[final].ClearField("target")
          self.generated_sbom_artifacts[final].targets.extend(
              archive_summary.get("targets"))

      generated_sbom_artifacts_json = {
          k: jsonpb.MessageToDict(a, preserving_proto_field_name=True)
          for k, a in self.generated_sbom_artifacts.items()
      }

      info_step = self.m.step.empty("SBOM's generated")
      if depbot_execution_summary.get("targets") is None:
        info_step.presentation.status = self.m.step.INFRA_FAILURE
        info_step.presentation.step_text = 'no targets found'
        return generated_sbom_artifacts_json


      # set generated in output properties
      info_step.presentation.logs['ssci_generated_artifacts'] = [
          f"{k}:{v}" for k, v in generated_sbom_artifacts_json.items()
      ]
      return generated_sbom_artifacts_json

  def merge_sboms(
      self,
      name,
      chrome_version=None,
      sbom_paths=None,
      platform=None,
  ):

    self._setup_ssci_tools(tools=[self.ssci_sbom])

    if platform is None:
      platform = f"{self.m.platform.name}_{self.m.platform.arch}{self.m.platform.bits}"

    recipe_name = self.m.properties["recipe"].split("/")[-1]
    product = f'{recipe_name}.{self.execution_id}.{name}'
    p_version = self._get_product_version(chrome_version)

    spdx_file = self.m.path.mkdtemp().joinpath("spdx-out.json")

    document_path_args = []
    for sbom_path in sbom_paths:
      document_path_args.extend(["-document-path", sbom_path])

    # Merge SBOMs using the SSCI SBOM Generator.
    self.m.step(
        'run SSCI SBOM Generator to merge SBOMs',
        [
            self.ssci_sbom.tool_path,
            "-output-file",
            spdx_file,
            "-sbom-generator-version",
            self.ssci_sbom.resolved_version,
            "-product",
            product,
            "-product-version",
            p_version,
            "-platform",
            platform,
        ] + document_path_args,
        infra_step=True,
        step_test_data=(lambda: self.m.json.test_api.output(
            data=[{
                "spdx": "yes"
            }], name="ssci_sbom_spdx")))

    spdx_digest = self.m.file.file_hash(spdx_file, test_data='testhash')

    generated_sbom_json = jsonpb.MessageToDict(
        GeneratedSBOM(
            digest=spdx_digest,
            filename=name,
            sbom_name=f"{name}{SBOM_EXTENSION}",
            sbom_path=f'{spdx_file}',
        ),
        preserving_proto_field_name=True)

    info_step = self.m.step.empty("SBOM's generated")
    info_step.presentation.logs[name] = self.m.json.dumps(generated_sbom_json)

    return generated_sbom_json

  def generate_sbom_for_artifacts(
      self,
      artifacts,
      source_dir: Path,
      build_dir: Path,
      gn_targets,
      platform=None,
      run_comparison=False,
  ):
    """Generates an SBOM for the given artifact based on the supplied GN targets.

    The generated SBOM file is written to the same directory as the artifact.

    Args:
      artifacts: A dict {name of the artifact: on disk path to the artifact}
      source_dir: The path to the source directory.
      build_dir: The path to the build directory.
      gn_targets: A list of GN targets to build the SBOM with.
      platform: The platform the artifact was built for.
      run_comparison: [DEPRECATED] Whether to also generate the SBOM using the
      SSCI SBOM Generator (Go), then compare it to the SBOM generated by the
      SSCI tool (Python).

    Returns:
      A dict of GeneratedSBOM protobuf message, where the sbom name is the key.
    """

    version = self.m.chromium.get_version(source_dir)
    chrome_version = f"{version['MAJOR']}.{version['MINOR']}.{version['BUILD']}.{version['PATCH']}"

    with self.m.context(env=self.m.chromium.get_env(source_dir)):
      results = self.run(
          src_dir=source_dir,
          build_dir=build_dir,
          chrome_version=chrome_version,
          targets=gn_targets,
          archive_names=artifacts.keys(),
          skip_targets=True,  # only create SBOMs for the archive artifacts
          platform=platform)

      sboms = {}

      for artifact_name, artifact_path in artifacts.items():
        sbom_expected = artifact_name + SBOM_EXTENSION

        for v in results.values():
          sbom = jsonpb.ParseDict(
              v, GeneratedSBOM(), ignore_unknown_fields=True)
          if sbom.sbom_name == sbom_expected:
            renamed_sbom_path = self.m.path.join(
                self.m.path.dirname(artifact_path), sbom.sbom_name)
            self.m.file.move("move and rename SBOM to match artifact",
                             sbom.sbom_path, renamed_sbom_path)
            sbom.sbom_path = renamed_sbom_path
            sboms[sbom.sbom_name] = sbom
            break
        if sbom_expected not in sboms:
          raise recipe_api.StepFailure(
              f'SBOM {sbom_expected} not found in generated SBOMs dict: {results}'
          )

      return sboms

  def modify_sbom(self, sbom_path, name):
    """Modifies an existing SBOM.

    Uses the SSCI SBOM Generator to modify the provided SBOM.

    Args:
      sbom_path: Path to the existing SBOM file.
      name: The name to use for the product identifier.

    Returns:
      A dictionary representation of the GeneratedSBOM protobuf message.
    """

    self._setup_ssci_tools(tools=[self.ssci_sbom])

    platform = f"{self.m.platform.name}_{self.m.platform.arch}{self.m.platform.bits}"

    recipe_name = self.m.properties["recipe"].split("/")[-1]
    product = f'{recipe_name}.{self.execution_id}.{name}'

    spdx_file = self.m.path.mkdtemp().joinpath("spdx-out.json")

    self.m.step(
        'run SSCI SBOM Generator to modify the provided SBOM', [
            self.ssci_sbom.tool_path, "-output-file", spdx_file,
            "-sbom-generator-version", self.ssci_sbom.resolved_version,
            "-product", product, "-platform", platform, "-document-path",
            sbom_path
        ],
        infra_step=True,
        step_test_data=(lambda: self.m.json.test_api.output(
            data=[{
                "spdx": "yes"
            }], name="ssci_sbom_spdx")))

    spdx_digest = self.m.file.file_hash(spdx_file, test_data='testhash')

    generated_sbom_json = jsonpb.MessageToDict(
        GeneratedSBOM(
            digest=spdx_digest,
            filename=name,
            sbom_name=f"{name}{SBOM_EXTENSION}",
            sbom_path=f'{spdx_file}',
        ),
        preserving_proto_field_name=True)

    info_step = self.m.step.empty("SBOM's generated")
    info_step.presentation.logs[name] = self.m.json.dumps(generated_sbom_json)

    return generated_sbom_json
