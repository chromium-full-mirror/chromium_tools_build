# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Uploads WPT Test results from Chromium CI to wpt.fyi.

This recipe runs the wpt-upload script. The upload process involves
first fetching the latest test results from Chromium CI, then upload
the result to wpt.fyi.

"""

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.depot_tools import bot_update, gclient
from RECIPE_MODULES.infra import cloudkms
from RECIPE_MODULES.recipe_engine import path, step


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  chromium: chromium.API
  cloudkms: cloudkms.API
  gclient: gclient.API
  path: path.API
  step: step.API

# See wpt_import.py for details.
CREDS_NAME = 'wpt-import-export'
KMS_CRYPTO_KEY = (
    'projects/chops-kms/locations/global/keyRings/%s/cryptoKeys/default' %
    CREDS_NAME)


def RunSteps(api: DEPS):
  api.gclient.set_config('chromium')
  update_result = api.bot_update.ensure_checkout()
  creds = api.path.cleanup_dir.joinpath(CREDS_NAME + '.json')
  api.cloudkms.decrypt(
      KMS_CRYPTO_KEY,
      api.repo_resource('recipes', 'recipes', 'assets', CREDS_NAME),
      creds,
  )

  script = update_result.source_root.path.joinpath('third_party', 'blink',
                                                   'tools', 'wpt_upload.py')
  args = ['--credentials-json', creds]
  cmd = ['vpython3', script] + args
  api.step('Upload WPT Result from Chromium CI to wpt.fyi', cmd)


# Run `./recipes.py test train` to update wpt-upload.json file.
def GenTests(api: RecipeTestApi):
  yield api.test('wpt-upload')
