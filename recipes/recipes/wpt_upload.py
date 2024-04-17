# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Uploads WPT Test results from Chromium CI to wpt.fyi.

This recipe runs the wpt-upload script. The upload process involves
first fetching the latest test results from Chromium CI, then upload
the result to wpt.fyi.

"""

DEPS = [
    'chromium',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'infra/cloudkms',
    'recipe_engine/path',
    'recipe_engine/step',
]

# See wpt_import.py for details.
CREDS_NAME = 'wpt-import-export'
KMS_CRYPTO_KEY = (
    'projects/chops-kms/locations/global/keyRings/%s/cryptoKeys/default' %
    CREDS_NAME)


def RunSteps(api):
  api.gclient.set_config('chromium')
  api.bot_update.ensure_checkout()
  creds = api.path.cleanup_dir.join(CREDS_NAME + '.json')
  api.cloudkms.decrypt(
      KMS_CRYPTO_KEY,
      api.repo_resource('recipes', 'recipes', 'assets', CREDS_NAME),
      creds,
  )

  script = api.path.checkout_dir.join('third_party', 'blink', 'tools',
                                      'wpt_upload.py')
  args = ['--credentials-json', creds]
  cmd = ['vpython3', script] + args
  api.step('Upload WPT Result from Chromium CI to wpt.fyi', cmd)


# Run `./recipes.py test train` to update wpt-upload.json file.
def GenTests(api):
  yield api.test('wpt-upload')
