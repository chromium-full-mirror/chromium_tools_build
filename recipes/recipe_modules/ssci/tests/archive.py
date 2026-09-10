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
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  source_dir = api.path.cache_dir / 'builder/src'

  res = api.ssci.run(
    src_dir=api.path.abspath(source_dir),
    build_dir='out/Release',
    sbom_bucket='my-bucket',
    sbom_folder='os/version',
    sbom_filename_postfix='Canary',
    targets=["Example.apk", "Another.apk"],
    archive_names=["example.zip", "example2.zip"],
  )

  api.assertions.assertEqual(
    res,
    {
      'ExampleCanary.apk': {
        'digest': 'testhash',
        'file': 'gs://my-bucket/os/version/luci-8945511751514863184/ExampleCanary.apk.spdx.json',
        'filename': 'ExampleCanary.apk',
        'sbom_name': 'ExampleCanary.apk.spdx.json',
        'sbom_path': '[CLEANUP]/tmp_tmp_2/spdx-out.json',
        'target': 'Example.apk',
      },
      'AnotherCanary.apk': {
        'digest': 'testhash',
        'file': 'gs://my-bucket/os/version/luci-8945511751514863184/AnotherCanary.apk.spdx.json',
        'filename': 'AnotherCanary.apk',
        'sbom_name': 'AnotherCanary.apk.spdx.json',
        'sbom_path': '[CLEANUP]/tmp_tmp_3/spdx-out.json',
        'target': 'Another.apk',
      },
      'exampleCanary.zip': {
        'digest': 'testhash',
        'file': 'gs://my-bucket/os/version/luci-8945511751514863184/exampleCanary.zip.spdx.json',
        'filename': 'exampleCanary.zip',
        'sbom_name': 'exampleCanary.zip.spdx.json',
        'sbom_path': '[CLEANUP]/tmp_tmp_4/spdx-out.json',
        'targets': [
          '//example:example',
          '//another:another',
        ],
      },
      'example2Canary.zip': {
        'digest': 'testhash',
        'file': 'gs://my-bucket/os/version/luci-8945511751514863184/example2Canary.zip.spdx.json',
        'filename': 'example2Canary.zip',
        'sbom_name': 'example2Canary.zip.spdx.json',
        'sbom_path': '[CLEANUP]/tmp_tmp_5/spdx-out.json',
        'targets': [
          '//example:example',
          '//another:another',
        ],
      },
    },
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic-archive',
    api.buildbucket.ci_build(
      project='myproject', bucket='mybucket', builder='mybuilder'
    ),
    api.override_step_data(
      'SSCI collection.run depbot',
      api.json.output(
        name="summary",
        data={
          "targets": [
            {
              "entry_point": "Example.apk",
              "target": "//example:example",
              "artifacts_file_path": "out/Release/artifacts.json",
              "libraries_file_path": "out/Release/libs.json",
            },
            {
              "entry_point": "Another.apk",
              "target": "//another:another",
              "artifacts_file_path": "out/Release/artifacts.json",
              "libraries_file_path": "out/Release/libs.json",
            },
          ],
          "archive": {
            "name": "generic_archive",
            "targets": ["//example:example", "//another:another"],
            "artifacts_file_path": "out/Release/artifacts.json",
            "libraries_file_path": "out/Release/libs.json",
          },
        },
      ),
    ),
    api.override_step_data(
      'SSCI collection.target specific steps for Example.apk.run SSCI SBOM Generator for Example.apk SBOM',
      api.json.output(
        name='basic.luci-8945511751514863184.example.apk', data={}
      ),
    ),
    api.override_step_data(
      'SSCI collection.target specific steps for Another.apk.run SSCI SBOM Generator for Another.apk SBOM',
      api.json.output(
        name='basic.luci-8945511751514863184.another.apk', data={}
      ),
    ),
    api.override_step_data(
      'SSCI collection.target specific steps for example.zip.run SSCI SBOM Generator for example.zip SBOM',
      api.json.output(
        name='basic.luci-8945511751514863184.example.zip', data={}
      ),
    ),
    api.post_process(
      post_process.MustRun,
      "SSCI collection.target specific steps for Example.apk",
    ),
    api.post_process(
      post_process.MustRun,
      "SSCI collection.target specific steps for Another.apk",
    ),
    api.post_process(
      post_process.MustRun,
      "SSCI collection.target specific steps for example.zip",
    ),
    api.properties(
      **{
        '$build/ssci': {
          "bq_artifact_table": "project.dataset.table",
          "bq_library_table": "project.dataset.table",
          "depbot_version": "latest",
          "targets": ["//example:example", "//another:another"],
          "partybot_version": "AABBCC",
          "bq_thirdparty_table": "project.dataset.table",
          "ssci_version": "latest",
        }
      }
    ),
  )
