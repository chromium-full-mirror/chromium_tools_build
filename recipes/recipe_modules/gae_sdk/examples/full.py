# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
  'gae_sdk',
  'recipe_engine/path',
  'recipe_engine/platform',
  'recipe_engine/step',
]


def RunSteps(api):
  api.step('all_packages', [])
  api.step.active_result.presentation.logs['details'] = [
    '%r: %r' % (plat, arch) for plat, arch in api.gae_sdk.all_packages
  ]

  for plat in api.gae_sdk.platforms:
    out = api.gae_sdk.repo_resource(
        'gae_sdk', '%s_%s' % (plat, api.platform.name))
    try:
      api.gae_sdk.fetch(plat, out)
    except api.gae_sdk.PackageNotFound:
      api.step.empty(
          'Failed to fetch',
          status=api.step.FAILURE,
          step_text=('No %s package for %s / %s' %
                     (plat, api.platform.name, api.platform.bits)))


def GenTests(api):
  yield api.test(
      'win',
      api.platform('win', 64),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'linux',
      api.platform('linux', 64),
  )

  yield api.test(
      'mac',
      api.platform('mac', 64),
  )
