# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, StepCommandContains

DEPS = [
    'chromium',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/runtime',
    'ssci',
]


def RunSteps(api):

  api.gclient.set_config('chromium')
  api.chromium.set_config(
      'chromium',
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'))

  update_result = api.bot_update.ensure_checkout()
  source_dir = update_result.source_root.path

  api.ssci.generate_sbom_for_artifact(
      artifact_name='chromium.zip',
      artifact_path=source_dir / 'Release/out' / 'chromium.zip',
      source_dir=source_dir,
      build_dir=source_dir / 'Release/out',
      gn_targets=['chromium', 'chromium_installer'],
      platform='linux',
  )


def GenTests(api):

  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.override_step_data(
          "SSCI collection.run depbot",
          api.json.output(
              name="summary",
              data={
                  "targets": [{
                      "entry_point": "chromium_installer",
                      "target": "//example:example",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }, {
                      "entry_point": "chromium",
                      "target": "//another:another",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }],
                  "archive": {
                      "name": "chromium.zip",
                      "targets": ["example", "another"],
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  },
              })),
      api.post_process(
          StepCommandContains,
          'move and rename SBOM to match artifact',
          [
              "vpython3", "-u",
              "RECIPE_MODULE[recipe_engine::file]/resources/fileutil.py",
              "--json-output", "/path/to/tmp/json", "move",
              "[CLEANUP]/tmp_tmp_5/spdx-out.json",
              "[START_DIR]/src/Release/out/chromium.zip.spdx.json"
          ],
      ),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'missing artifact',
      api.chromium.ci_build(
          builder_group='test_group',
          builder='test_buildername',
      ),
      api.properties(custom_vars={
          'gcs_bucket': 'my-custom-var-bucket',
      }),
      api.override_step_data(
          "SSCI collection.run depbot",
          api.json.output(
              name="summary",
              data={
                  "targets": [{
                      "entry_point": "Example.apk",
                      "target": "//example:example",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }, {
                      "entry_point": "Another.apk",
                      "target": "//another:another",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }],
                  "archive": {
                      "name": "not-exist.zip",
                      "targets": ["example", "example_2"],
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  },
              })),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
