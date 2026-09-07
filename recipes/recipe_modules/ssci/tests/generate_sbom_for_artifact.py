# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DoesNotRunRE, DropExpectation, MustRunRE, StepCommandContains

from PB.recipe_modules.build.ssci.properties import GeneratedSBOM

from google.protobuf import json_format as jsonpb

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, ssci
from RECIPE_MODULES.depot_tools import bot_update, gclient
from RECIPE_MODULES.recipe_engine import (
    assertions,
    json,
    path,
    properties,
    runtime,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  bot_update: bot_update.API
  chromium: chromium.API
  gclient: gclient.API
  json: json.API
  path: path.API
  properties: properties.API
  runtime: runtime.API
  ssci: ssci.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  json: json.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):

  api.gclient.set_config('chromium')
  api.chromium.set_config(
      'chromium',
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'))

  update_result = api.bot_update.ensure_checkout()
  source_dir = update_result.source_root.path

  res = api.ssci.generate_sbom_for_artifacts(
      artifacts={
          'chromium.zip': source_dir / 'Release/out' / 'chromium.zip',
          'remoting.zip': source_dir / 'Release/out/special' / 'remoting.zip'
      },
      source_dir=source_dir,
      build_dir=source_dir / 'Release/out',
      gn_targets=['chromium', 'chromium_installer'],
      platform='linux',
  )

  api.assertions.assertEqual(
      res, {
          'chromium.zip.spdx.json':
              jsonpb.ParseDict(
                  {
                      'digest':
                          'testhash',
                      'filename':
                          'chromium.zip',
                      'sbom_name':
                          'chromium.zip.spdx.json',
                      'sbom_path':
                          '[START_DIR]/src/Release/out/chromium.zip.spdx.json',
                      'targets': ['example', 'another'],
                  }, GeneratedSBOM()),
          'remoting.zip.spdx.json':
              jsonpb.ParseDict(
                  {
                      'digest':
                          'testhash',
                      'filename':
                          'remoting.zip',
                      'sbom_name':
                          'remoting.zip.spdx.json',
                      'sbom_path':
                          '[START_DIR]/src/Release/out/special/remoting.zip.spdx.json',
                      'targets': ['example', 'another'],
                  }, GeneratedSBOM())
      })


def GenTests(api: TEST_DEPS):

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
                      "name": "generic_archive",
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
              "[CLEANUP]/tmp_tmp_2/spdx-out.json",
              "[START_DIR]/src/Release/out/chromium.zip.spdx.json"
          ],
      ),
      api.post_process(
          StepCommandContains,
          'move and rename SBOM to match artifact (2)',
          [
              "vpython3", "-u",
              "RECIPE_MODULE[recipe_engine::file]/resources/fileutil.py",
              "--json-output", "/path/to/tmp/json", "move",
              "[CLEANUP]/tmp_tmp_3/spdx-out.json",
              "[START_DIR]/src/Release/out/special/remoting.zip.spdx.json"
          ],
      ),
      api.post_process(
          MustRunRE,
          r'.+\.run SSCI SBOM Generator for .+ SBOM',
          at_least=2,
          at_most=2,
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
                      "name": "not-exists.zip",
                      "targets": ["example", "example_2"],
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  },
              })),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
