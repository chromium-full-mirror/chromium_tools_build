# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import ssci
from RECIPE_MODULES.recipe_engine import (
    assertions,
    buildbucket,
    context,
    json,
    path,
    properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  buildbucket: buildbucket.API
  context: context.API
  json: json.API
  path: path.API
  properties: properties.API
  ssci: ssci.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  json: json.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  source_dir = api.path.cache_dir / 'builder/src'

  custom_versions = {
      'infra_internal/tools/security/depbot/${platform}': '123',
      'infra_internal/tools/partybot': '456',
      # The ssci tool has been deprecated, but specifying its version should not
      # break anything.
      'infra_internal/tools/ssci': '789',
      'infra_internal/tools/security/ssci_sbom/${platform}': '1001',
  }

  with api.ssci.custom_tool_versions(custom_versions):
    api.ssci.run(
        src_dir=api.path.abspath(source_dir),
        build_dir='out/Release',
        sbom_bucket='my-bucket',
        sbom_folder='os/version',
        sbom_filename_postfix='Stable',
        targets=["//example:example"])


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.buildbucket.ci_build(
          project='myproject', bucket='mybucket', builder='mybuilder'),
      api.override_step_data(
          'SSCI collection.run depbot',
          api.json.output(
              name="summary",
              data={
                  "targets": [{
                      "entry_point": "Example.apk",
                      "target": "//example:example",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }],
              })),
      api.post_process(
          post_process.LogContains,
          'SSCI collection.install infra_internal/tools/security/depbot.ensure_installed',
          'json.output',
          ['"instance_id": "resolved-instance_id-of-123-------------"']),
      api.post_process(
          post_process.LogContains,
          'SSCI collection.install infra_internal/tools/partybot.ensure_installed',
          'json.output',
          ['"instance_id": "resolved-instance_id-of-456-------------"']),
      api.post_process(
          post_process.LogContains,
          'SSCI collection.install infra_internal/tools/security/ssci_sbom.ensure_installed',
          'json.output',
          ['"instance_id": "resolved-instance_id-of-1001------------"']),
      api.override_step_data(
          'SSCI collection.target specific steps for Example.apk.run SSCI SBOM Generator for Example.apk SBOM',
          api.json.output(
              name='basic.luci-8945511751514863184.example.apk', data={})),
      api.properties(
          **{
              '$build/ssci': {
                  "bq_artifact_table": "project.dataset.table",
                  "bq_library_table": "project.dataset.table",
                  "depbot_version": "latest",
                  "targets": ["//example:example"],
                  "partybot_version": "AABBCC",
                  "bq_thirdparty_table": "project.dataset.table",
                  "ssci_version": "latest",
                  "ssci_sbom_version": "latest",
              }
          }))
