# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
  'depot_tools/bot_update',
  'chromium',
  'depot_tools/gclient',
  'recipe_engine/buildbucket',
  'recipe_engine/context',
  'recipe_engine/path',
  'recipe_engine/properties',
  'recipe_engine/raw_io',
  'recipe_engine/service_account',
  'recipe_engine/step',
  'recipe_engine/url',
  'v8',
]

def RunSteps(api):
  api.gclient.set_config('v8')
  update_result = api.v8.checkout()

  source_dir = update_result.source_root.path
  with api.context(cwd=source_dir, env={'SKIP_GCE_AUTH_FOR_GIT': '1'}):
    safe_buildername = ''.join(
        c if c.isalnum() else '_' for c in api.buildbucket.builder_name)
    push_account = (
        # TODO(sergiyb): Replace with api.service_account.default().get_email()
        # when https://crbug.com/846923 is resolved.
        'v8-ci-autoroll-builder@chops-service-accounts.iam.gserviceaccount.com')
    api.v8.python(
        'push candidate',
        source_dir.joinpath('tools', 'release', 'auto_push.py'),
        [
            '--push', '--author', push_account, '--reviewer', push_account,
            '--work-dir',
            api.path.cache_dir.joinpath(safe_buildername, 'workdir')
        ],
    )


def GenTests(api):
  yield api.test(
      'standard',
      api.expect_status('SUCCESS'),
  )
