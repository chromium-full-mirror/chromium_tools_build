# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium_gerrit_utils',
    'depot_tools/gerrit',
]


def RunSteps(api):
  api.chromium_gerrit_utils.create_temp_cl('some/file/path', 'some-topic')
  api.chromium_gerrit_utils.abandon_old_cls('some-topic', '24h')


def GenTests(api):
  yield api.test(
      'basic',
      api.override_step_data(
          'gerrit create change at (chromium/src main)',
          api.gerrit.update_files_response_data(change_number=123456)),
      api.step_data(
          'abandon old CLs.gerrit changes',
          api.gerrit.get_one_change_response_data(change_number=654321)),
      api.post_check(post_process.MustRun,
                     'abandon old CLs.gerrit abandon 654321'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'abandon_old_cls_failure',
      api.override_step_data(
          'gerrit create change at (chromium/src main)',
          api.gerrit.update_files_response_data(change_number=123456)),
      api.step_data(
          'abandon old CLs.gerrit changes',
          api.gerrit.get_one_change_response_data(change_number=654321)),
      # A failure to abandon an old CL shouldn't fail the build.
      api.step_data('abandon old CLs.gerrit abandon 654321', retcode=1),
      api.post_process(post_process.DropExpectation),
  )
