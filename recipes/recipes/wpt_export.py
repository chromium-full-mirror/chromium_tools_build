# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Exports commits in Chromium to the web-platform-tests repo.

This recipe runs the wpt-export script; it is expected to be run as a
recurring job at a short interval. It creates pull requests on GitHub
for Chromium commits that contain exportable changes, merges these
pull requests.

See: //docs/testing/web_platform_tests.md (https://goo.gl/rSRGmZ)
"""

from PB.go.chromium.org.luci.buildbucket.proto import common
from PB.recipe_engine.result import RawResult

DEPS = [
    'chromium',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'infra/cloudkms',
    'recipe_engine/file',
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
                                      'wpt_export.py')
  summary_path = api.path.mkstemp()
  args = [
      '--credentials-json',
      creds,
      '--surface-failures-to-gerrit',
      '--summary-markdown',
      str(summary_path),
  ]
  cmd = ['vpython3', script] + args
  api.step('Export Chromium commits and in-flight CLs to WPT', cmd)
  summary_contents = api.file.read_text(
      'read summary of PRs modified',
      summary_path,
      test_data='No pull requests modified.\n')
  return RawResult(status=common.SUCCESS, summary_markdown=summary_contents)


# Run `./recipes.py test train` to update wpt-export.json file.
def GenTests(api):
  yield api.test('wpt-export')
