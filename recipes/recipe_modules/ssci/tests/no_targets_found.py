# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'ssci',
]


def RunSteps(api):
  source_dir = api.path.cache_dir / 'builder/src'

  api.ssci.run(
      src_dir=api.path.abspath(source_dir),
      build_dir='out/Release',
      sbom_bucket='my-bucket',
      sbom_folder='os/version',
      sbom_filename_postfix='Stable',
      targets=["//example:example"])

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
                  '[CLEANUP]/tmp_tmp_5/spdx-out.json',
              'target':
                  'Example.apk'
          }
      })


def GenTests(api):
  yield api.test(
      'depbot-output-empty',
      api.buildbucket.ci_build(
          project='myproject', bucket='mybucket', builder='mybuilder'),
      api.override_step_data('SSCI collection.run depbot',
                             api.json.output(name="summary", data={})),
      api.post_process(post_process.StepException,
                       "SSCI collection.SBOM's generated"),
      api.post_process(post_process.DropExpectation))
