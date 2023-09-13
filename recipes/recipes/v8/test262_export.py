# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Exports commits in V8 to the test262 repo.

This recipe runs the export script; it is expected to be run as a recurring job
at a short interval. It creates pull requests on GitHub for V8 commits that
contain exportable changes and merges these pull requests.
"""

DEPS = [
    'depot_tools/gclient',
    'infra/cloudkms',
    'recipe_engine/path',
    'recipe_engine/step',
    'v8',
]

CREDS_NAME = 'test262-import-export'
KMS_CRYPTO_KEY = (
    'projects/v8-infra/locations/global/keyRings/%s/cryptoKeys/default' %
    CREDS_NAME)


def RunSteps(api):
  api.gclient.set_config('chromium')
  api.gclient.apply_config('v8_tot')
  api.v8.checkout(ignore_input_commit=True, set_output_commit=False)

  creds = api.path['cleanup'].join(CREDS_NAME + '.json')
  api.cloudkms.decrypt(
      KMS_CRYPTO_KEY,
      api.repo_resource('recipes', 'recipes', 'assets', CREDS_NAME),
      creds,
  )

  chromium_path = api.path['checkout']
  blink_tools_path = chromium_path.join('third_party', 'blink', 'tools')

  v8_path = chromium_path.join('v8')
  script = v8_path.join('test', 'test262', 'tools', 'export.py')


  args = [
      '--credentials-json',
      creds,
      '--surface-failures-to-gerrit',
      '--blink-tools-path',
      blink_tools_path,
      # TODO: Remove this once we have a stable config.
      #'--config-path', api.repo_resource('recipes', 'recipes', 'assets', 'v8configs.json'),
  ]
  api.v8.vpython('Export V8 commits to Test262', script, args)


def GenTests(api):
  yield api.test('test262-export')
