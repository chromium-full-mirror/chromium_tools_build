# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import StepCommandContains

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
  modified = api.ssci.modify_sbom(
      name="modified",
      sbom_path="path1/path.spdx.json",
  )

  api.assertions.assertEqual(
      modified, {
          "digest": "testhash",
          "filename": "modified",
          "sbom_name": "modified.spdx.json",
          "sbom_path": "[CLEANUP]/tmp_tmp_1/spdx-out.json"
      })


def GenTests(api):
  yield api.test(
      'basic',
      api.buildbucket.ci_build(
          project='myproject', bucket='mybucket', builder='mybuilder'),
      api.post_process(
          StepCommandContains,
          'run SSCI SBOM Generator to modify the provided SBOM', [
              '-output-file', '[CLEANUP]/tmp_tmp_1/spdx-out.json',
              '-sbom-generator-version',
              'vresolved-instance_id-of-prod------------', '-product',
              'modify..modified', '-platform', 'linux_intel64',
              '-document-path', 'path1/path.spdx.json'
          ]),
      api.override_step_data(
          'run SSCI SBOM Generator to modify the provided SBOM',
          api.json.output(name='[CLEANUP]/tmp_tmp_1/spdx-out.json', data={})))
