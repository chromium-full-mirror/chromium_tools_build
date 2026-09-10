# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, v8
from RECIPE_MODULES.depot_tools import bot_update, gclient
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  path,
  properties,
  raw_io,
  service_account,
  step,
  url,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  context: context.API
  gclient: gclient.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  service_account: service_account.API
  step: step.API
  url: url.API
  v8: v8.API


def RunSteps(api: DEPS):
  api.gclient.set_config('v8')
  update_result = api.v8.checkout()

  source_dir = update_result.source_root.path
  with api.context(cwd=source_dir, env={'SKIP_GCE_AUTH_FOR_GIT': '1'}):
    safe_buildername = ''.join(
      c if c.isalnum() else '_' for c in api.buildbucket.builder_name
    )
    push_account = (
      # TODO(sergiyb): Replace with api.service_account.default().get_email()
      # when https://crbug.com/846923 is resolved.
      'v8-ci-autoroll-builder@chops-service-accounts.iam.gserviceaccount.com'
    )
    api.v8.python(
      'push candidate',
      source_dir.joinpath('tools', 'release', 'auto_push.py'),
      [
        '--push',
        '--author',
        push_account,
        '--reviewer',
        push_account,
        '--work-dir',
        api.path.cache_dir.joinpath(safe_buildername, 'workdir'),
      ],
    )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'standard',
    api.expect_status('SUCCESS'),
  )
