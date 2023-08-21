# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'ssci',
]


def RunSteps(api):
  api.ssci.run(
      src_dir=api.path.abspath(api.path['checkout']),
      build_dir='out/Release',
      sbom_bucket='my-bucket',
      sbom_folder='os/version',
      sbom_filename_postfix='Stable',
      targets=["//example:example"])

  api.ssci.run(
      src_dir=api.path.abspath(api.path['checkout']),
      build_dir='out/Release',
      sbom_bucket='my-bucket',
      sbom_folder='os/version',
      sbom_filename_postfix='Canary',
      targets=["//example:example"])


def GenTests(api):
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
                      "entry_point": "example.apk",
                      "target": "//example:example",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }],
              })),
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
              }
          }))

  yield api.test(
      'minimal-sdpx',
      api.buildbucket.ci_build(
          project='myproject', bucket='mybucket', builder='mybuilder'),
      api.override_step_data(
          'SSCI collection.run depbot',
          api.json.output(
              name="summary",
              data={
                  "targets": [{
                      "entry_point": "//example:example",
                      "target": "//example:example",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }],
              })),
      api.properties(
          **{
              '$build/ssci': {
                  "bq_artifact_table": "project.dataset.table",
                  "bq_library_table": "project.dataset.table",
                  "bq_thirdparty_table": "project.dataset.table",
                  "minimal_spdx": True,
              }
          }), api.post_process(DropExpectation))
