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
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/scheduler',
    'recipe_engine/step',
    'v8',
]

EXPORTER_CREDS_NAME = 'test262-import-export'
APPROVER_CREDS_NAME = 'test262-approve'
KMS_CRYPTO_KEY = ('projects/v8-infra/locations/global/keyRings/'
                  'test262-import-export/cryptoKeys/default')


def RunSteps(api):
  configure(api)

  api.v8.checkout()

  creds_file = api.path['cleanup'].join('test262.json')
  is_approver = api.properties.get('approver', False)
  creds_name = APPROVER_CREDS_NAME if is_approver else EXPORTER_CREDS_NAME
  api.cloudkms.decrypt(
      KMS_CRYPTO_KEY,
      api.repo_resource('recipes', 'recipes', 'v8', 'assets', creds_name),
      creds_file,
  )

  checkout_root = api.path['cache'].join('builder')
  chromium_path = checkout_root.join('src')
  blink_tools_path = chromium_path.join('third_party', 'blink', 'tools')

  v8_path = checkout_root.join('v8')

  script = v8_path.join('test', 'test262', 'tools', 'export.py')

  with api.context(cwd=v8_path):
    args = [
        '--credentials-json',
        creds_file,
        '--surface-failures-to-gerrit',
        '--blink-tools-path',
        blink_tools_path,
    ]

    if is_approver:
      api.v8.vpython('Approve exported PRs in Test262', script,
                     args + ['--approver'])
    else:
      api.v8.vpython('Export V8 commits to Test262', script, args)
      api.scheduler.emit_trigger(
          api.scheduler.BuildbucketTrigger(),
          'v8',
          ['Test262 PR approver'],
          step_name='Trigger approver',
      )

def configure(api):
  api.gclient.set_config('chromium')
  api.gclient.apply_config('v8_bare')
  # TODO: Remove this in finalized version. This is just for testing.
  #api.gclient.c.revisions['v8'] = "49cd7d838c98245268b12d2c75538faa3e402ac0"


def GenTests(api):
  yield api.test('test262-export')
  yield api.test('test262-approve') + api.properties(approver=True)
