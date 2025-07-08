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
  source_dir = api.path.cache_dir / 'builder/src'
  api.ssci.run(
      src_dir=api.path.abspath(source_dir),
      build_dir='out/Release',
      targets=["ChromeRemoteDesktop.apk"],
      sbom_filename_postfix=None,
      sbom_bucket='my-bucket',
      sbom_folder='os/version/',
      chrome_version="111.111.111.11")

def GenTests(api):
  yield api.test(
      'non-channel',
      api.buildbucket.ci_build(
          project='myproject', bucket='mybucket', builder='mybuilder'),
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
          }),
  )
