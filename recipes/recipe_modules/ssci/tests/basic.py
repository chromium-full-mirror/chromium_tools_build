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

  api.ssci.run(
      src_dir=api.path.abspath(source_dir),
      build_dir='out/Release',
      sbom_bucket='my-bucket',
      sbom_folder='os/version',
      sbom_filename_postfix='Stable',
      targets=["//example:example"])

  # Mock Partybot output file existing after the first call to `run` for the
  # build.
  partybot_dir = api.path.cleanup_dir / 'partybot'
  out_file = 'third_party_luci-8945511751514863184.json'
  api.path.mock_add_file(partybot_dir / out_file)

  res = api.ssci.run(
      src_dir=api.path.abspath(source_dir),
      build_dir='out/Release',
      sbom_bucket='my-bucket',
      sbom_folder='os/version',
      sbom_filename_postfix='Canary',
      targets=["Example.apk"])

  api.assertions.assertEqual(
      res, {
          'ExampleCanary.apk': {
              'digest':
                  'testhash',
              'file':
                  'gs://my-bucket/os/version/luci-8945511751514863184/ExampleCanary.apk.spdx.json',
              'filename':
                  'ExampleCanary.apk',
              'sbom_name':
                  'ExampleCanary.apk.spdx.json',
              'sbom_path':
                  '[CLEANUP]/tmp_tmp_4/spdx-out.json',
              'target':
                  'Example.apk'
          }
      })


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
      api.override_step_data(
          'SSCI collection.target specific steps for Example.apk.run SSCI SBOM Generator for Example.apk SBOM',
          api.json.output(
              name='basic.luci-8945511751514863184.example.apk', data={})),
      api.override_step_data(
          'SSCI collection (2).target specific steps for Example.apk.run SSCI SBOM Generator for Example.apk SBOM',
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
              }
          }),
      api.post_process(
          post_process.MustRunRE,
          r'.+\.run partybot to collect 3P deps',
          at_least=1,
          at_most=1,
      ),
      api.post_process(
          post_process.MustRunRE,
          r'.+\.using existing Partybot results',
          at_least=1,
      ),
      api.post_process(
          post_process.StepCommandDoesNotContain,
          'SSCI collection.target specific steps for Example.apk.run SSCI SBOM Generator for Example.apk SBOM',
          ['-add-licenses'],
      ),
  )

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
                  "enable_upload_collected_data": True,
                  "minimal_spdx": True,
              }
          }), api.post_process(post_process.DropExpectation))

  yield api.test(
      'bq-upload-fail',
      api.buildbucket.ci_build(
          project='myproject', bucket='mybucket', builder='mybuilder'),
      api.step_data(
          'SSCI collection.upload third party dependencies to BigQuery',
          retcode=1),
      api.properties(
          **{
              '$build/ssci': {
                  "bq_artifact_table": "project.dataset.table",
                  "bq_library_table": "project.dataset.table",
                  "bq_thirdparty_table": "project.dataset.table",
                  "enable_upload_collected_data": True,
                  "minimal_spdx": True,
              }
          }),
      api.post_process(post_process.MustRun,
                       "SSCI collection.SBOM's generated"),
      api.post_process(post_process.DropExpectation))

  yield api.test(
      'collected_data_upload_disabled',
      api.buildbucket.ci_build(
          project='myproject', bucket='mybucket', builder='mybuilder'),
      api.properties(
          **{
              '$build/ssci': {
                  "bq_artifact_table": "project.dataset.table",
                  "bq_library_table": "project.dataset.table",
                  "bq_thirdparty_table": "project.dataset.table",
                  "enable_upload_collected_data": False,
                  "minimal_spdx": True,
              }
          }),
      api.post_process(
          post_process.DoesNotRun,
          "SSCI collection.upload third party dependencies to BigQuery"),
      api.post_process(post_process.DropExpectation))

  yield api.test(
      'with-licenses',
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
                  "include_licenses": True,
              }
          }),
      api.post_process(
          post_process.StepCommandContains,
          'SSCI collection.target specific steps for Example.apk.run SSCI SBOM Generator for Example.apk SBOM',
          ['-add-licenses'],
      ),
      api.post_process(
          post_process.StepCommandContains,
          'SSCI collection (2).target specific steps for Example.apk.run SSCI SBOM Generator for Example.apk SBOM',
          ['-add-licenses'],
      ),
  )

  yield api.test(
      'cipd-resolve-wrong-pin-count',
      api.buildbucket.ci_build(
          project='myproject', bucket='mybucket', builder='mybuilder'),
      api.override_step_data(
          'SSCI collection.cipd resolve infra_internal/tools/security/depbot/${platform}',
          api.json.output({'result': {
              '': []
          }}),
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       'expected only 1 resolved pin but got 0'),
      api.post_process(post_process.DropExpectation),
  )
