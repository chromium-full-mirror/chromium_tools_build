# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import StepCommandContains

from PB.recipe_modules.build.ssci.properties import GeneratedSBOM

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import ssci
from RECIPE_MODULES.recipe_engine import (
    assertions,
    buildbucket,
    json,
    path,
    properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  buildbucket: buildbucket.API
  json: json.API
  path: path.API
  properties: properties.API
  ssci: ssci.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  json: json.TEST_API


def RunSteps(api: DEPS):
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


def GenTests(api: TEST_DEPS):
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
