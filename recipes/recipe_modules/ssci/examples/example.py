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
      src_dir=api.path['checkout'],
      build_dir='out/Release',
      json_artifact_out=api.json.output(name='artifacts'),
      json_library_out=api.json.output(name='libraries'))


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
                  "target": "//base:base"
              }
          }))
