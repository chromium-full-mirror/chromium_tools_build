# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Exports commits in V8 to the test262 repo.

This recipe runs the export script; it is expected to be run as a recurring job
at a short interval. It creates pull requests on GitHub for V8 commits that
contain exportable changes and merges these pull requests.
"""

import json

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8, v8_auto_roller
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.infra import cloudkms
from RECIPE_MODULES.recipe_engine import (
  context,
  file,
  path,
  properties,
  scheduler,
  service_account,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  cloudkms: cloudkms.API
  context: context.API
  file: file.API
  gclient: gclient.API
  path: path.API
  properties: properties.API
  scheduler: scheduler.API
  service_account: service_account.API
  step: step.API
  v8: v8.API
  v8_auto_roller: v8_auto_roller.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  file: file.TEST_API
  gclient: gclient.TEST_API
  properties: properties.TEST_API


EXPORTER_CREDS_NAME = 'test262-import-export'
APPROVER_CREDS_NAME = 'test262-approve'
KMS_CRYPTO_KEY = (
  'projects/v8-infra/locations/global/keyRings/'
  'test262-import-export/cryptoKeys/default'
)


def RunSteps(api: DEPS):
  configure(api)

  api.v8.checkout()

  creds_file = api.path.cleanup_dir / 'test262.json'
  is_approver = api.properties.get('approver', False)
  creds_name = APPROVER_CREDS_NAME if is_approver else EXPORTER_CREDS_NAME
  api.cloudkms.decrypt(
    KMS_CRYPTO_KEY,
    api.repo_resource('recipes', 'recipes', 'v8', 'assets', creds_name),
    creds_file,
  )
  patch_gerrit_credentials(api, creds_file)

  checkout_root = api.path.cache_dir / 'builder'
  chromium_path = checkout_root / 'src'
  blink_tools_path = chromium_path.joinpath('third_party', 'blink', 'tools')

  v8_path = checkout_root / 'v8'

  script = v8_path.joinpath('test', 'test262', 'tools', 'export.py')

  with api.context(cwd=v8_path):
    args = [
      '--credentials-json',
      creds_file,
      '--surface-failures-to-gerrit',
      '--blink-tools-path',
      blink_tools_path,
    ]

    if is_approver:
      api.v8.vpython(
        'Approve exported PRs in Test262', script, args + ['--approver']
      )
    else:
      api.v8.vpython('Export V8 commits to Test262', script, args)


def configure(api: DEPS):
  api.gclient.set_config('chromium')
  api.gclient.apply_config('v8_bare')
  # TODO: Remove this in finalized version. This is just for testing.
  # api.gclient.c.revisions['v8'] = "49cd7d838c98245268b12d2c75538faa3e402ac0"


def patch_gerrit_credentials(api: DEPS, creds_file):
  # read the file as json
  creds = api.file.read_json(
    "read credetials", creds_file, test_data={}, include_log=False
  )
  creds['GERRIT_USER'] = api.v8_auto_roller.service_account
  creds['GERRIT_TOKEN'] = api.service_account.default().get_access_token(
    scopes=['https://www.googleapis.com/auth/gerritcodereview'],
  )
  api.file.write_json("update credetials", creds_file, creds, include_log=False)


def GenTests(api: TEST_DEPS):
  yield api.test('test262-export')
  yield api.test('test262-approve') + api.properties(approver=True)
