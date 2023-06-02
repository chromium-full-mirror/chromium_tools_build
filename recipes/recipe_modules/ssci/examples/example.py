# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

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
      json_artifact_out=api.json.output(name='artifacts'),
      json_library_out=api.json.output(name='libraries'),
      third_party_out=api.json.output(name='third_party'),
      spdx_out=api.json.output(name='spdx'),
      target="//example:example")


def GenTests(api):
  yield api.test(
      'basic',
      api.buildbucket.ci_build(
          project='myproject', bucket='mybucket', builder='mybuilder'),
      api.properties(
          **{
              '$build/ssci': {
                  "bq_artifact_table": "project.dataset.table",
                  "bq_library_table": "project.dataset.table",
                  "depbot_version": "latest",
                  "fast_mode": False,
                  "target": "//base:base",
                  "partybot_version": "AABBCC",
                  "bq_thirdparty_table": "project.dataset.table",
                  "ssci_version": "latest",
              }
          }))
