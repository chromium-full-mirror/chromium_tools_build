# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from PB.recipe_modules.build.ssci.properties import GeneratedSBOM

DEPS = [
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'ssci',
]


def RunSteps(api):
  merged = api.ssci.merge_sboms(
      name="merged",
      sbom_paths=["path1/path.spdx.json", "path2/path.spdx.json"],
  )

  api.assertions.assertEqual(
      merged, {
          "digest": "testhash",
          "filename": "merged",
          "sbom_name": "merged.spdx.json",
          "sbom_path": "[CLEANUP]/tmp_tmp_1/spdx-out.json"
      })


def GenTests(api):
  yield api.test(
      'basic',
      api.buildbucket.ci_build(
          project='myproject', bucket='mybucket', builder='mybuilder'),
      api.post_process(
          post_process.StepCommandContains, 'run ssci tool to merge SBOMs', [
              "vpython3", "--vpython-spec=.vpython3", "-m", "ssci", "spdx",
              "-ssci-version", "vresolved-instance_id-of-latest----------",
              "-output-file", "[CLEANUP]/tmp_tmp_1/spdx-out.json", "-product",
              "merge..merged", "-product-version", "2d7251", "-platform",
              "linux_intel64", "-document-paths", "path1/path.spdx.json",
              "path2/path.spdx.json"
          ]),
      api.override_step_data(
          'run ssci tool to merge SBOMs',
          api.json.output(name='[CLEANUP]/tmp_tmp_1/spdx-out.json', data={})))
