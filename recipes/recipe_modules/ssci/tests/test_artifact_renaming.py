# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'ssci',
]


def RunSteps(api):
  api.path.checkout_dir = api.path.cache_dir / 'builder' / 'src'
  api.ssci.run(
      src_dir=api.path.abspath(api.path.checkout_dir),
      build_dir='out/Release',
      targets=["SystemWebViewGoogle.apk"],
      sbom_filename_postfix="Stable",
      sbom_bucket='my-bucket',
      sbom_folder='os/version/',
      chrome_version="111.111.111.11",
      to_rename={"SystemWebViewGoogle": "AndroidWebview"})


def GenTests(api):
  yield api.test(
      'rename-systemwebview',
      api.buildbucket.ci_build(
          project='myproject', bucket='mybucket', builder='mybuilder'),
      api.properties(
          **{
              '$build/ssci': {
                  "bq_artifact_table": "project.dataset.table",
                  "bq_library_table": "project.dataset.table",
                  "depbot_version": "latest",
                  "targets": ["SystemWebViewGoogle.apk"],
                  "partybot_version": "AABBCC",
                  "bq_thirdparty_table": "project.dataset.table",
                  "ssci_version": "latest",
              }
          }),
      api.override_step_data(
          'SSCI collection.run depbot',
          api.json.output(
              name="summary",
              data={
                  "targets": [{
                      "entry_point":
                          "SystemWebViewGoogle.apk",
                      "target":
                          "//clank/android_webview:system_webview_google_bundle_apks",
                      "artifacts_file_path":
                          "out/Release/artifacts.json",
                      "libraries_file_path":
                          "out/Release/libs.json"
                  }],
              })),
      api.post_process(
          post_process.MustRun,
          "SSCI collection.target specific steps for AndroidWebview.apk (SystemWebViewGoogle.apk)"
      ),
  )
