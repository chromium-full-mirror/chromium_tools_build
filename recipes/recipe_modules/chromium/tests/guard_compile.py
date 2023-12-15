# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium

DEPS = [
    'chromium',
    'recipe_engine/file',
    'recipe_engine/path',
    'siso',
]


def RunSteps(api):
  api.chromium.set_config('chromium')
  api.path['checkout'] = api.path['cache'] / 'builder' / 'src'

  with api.chromium.guard_compile():
    api.chromium.mb_gen(
        chromium.BuilderId.create_for_group('fake-group', 'fake-builder'))
    return api.chromium.compile()


def GenTests(api):
  yield api.test('basic',
                 api.post_check(post_process.MustRun, 'create compile guard'),
                 api.post_check(post_process.MustRun, 'remove compile guard'),
                 api.post_process(post_process.DropExpectation))

  yield api.test('compile-failure',
                 api.override_step_data('compile', retcode=1),
                 api.post_check(post_process.MustRun, 'create compile guard'),
                 api.post_check(post_process.MustRun, 'remove compile guard'),
                 api.expect_status('FAILURE'),
                 api.post_process(post_process.DropExpectation))

  yield api.test(
      'generate-build-files-failure',
      api.override_step_data('generate_build_files', retcode=1),
      api.post_check(post_process.MustRun, 'create compile guard'),
      api.post_check(post_process.MustRun, 'remove compile guard'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cancel',
      api.override_step_data(
          'compile', retcode=1, global_shutdown_event='after'),
      api.post_check(post_process.MustRun, 'create compile guard'),
      api.post_check(post_process.DoesNotRun, 'remove compile guard'),
      api.expect_status('CANCELED'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'recovery',
      api.path.exists(api.path['checkout'].join('out', 'Release',
                                                'CR_COMPILE_GUARD.txt')),
      api.post_check(post_process.MustRun, 'remove unreliable output dir'),
      api.post_process(post_process.DropExpectation))

  yield api.test(
      'no_last_build_system',
      api.post_check(post_process.DoesNotRun, 'remove unreliable output dir'),
      api.post_process(post_process.DropExpectation))

  yield api.test(
      'build_system_siso_to_siso', api.siso.properties(),
      api.path.exists(api.path['checkout'].join('out', 'Release',
                                                'LAST_BUILD_SYSTEM.txt')),
      api.step_data('read LAST_BUILD_SYSTEM.txt', api.file.read_text('siso')),
      api.post_check(post_process.DoesNotRun, 'remove unreliable output dir'),
      api.post_process(post_process.DropExpectation))

  yield api.test(
      'build_system_ninja_to_ninja',
      api.path.exists(api.path['checkout'].join('out', 'Release',
                                                'LAST_BUILD_SYSTEM.txt')),
      api.step_data('read LAST_BUILD_SYSTEM.txt', api.file.read_text('ninja')),
      api.post_check(post_process.DoesNotRun, 'remove unreliable output dir'),
      api.post_process(post_process.DropExpectation))

  yield api.test(
      'build_system_ninja_to_siso', api.siso.properties(),
      api.path.exists(api.path['checkout'].join('out', 'Release',
                                                'LAST_BUILD_SYSTEM.txt')),
      api.step_data('read LAST_BUILD_SYSTEM.txt', api.file.read_text('ninja')),
      api.post_check(post_process.MustRun, 'remove unreliable output dir'),
      api.post_process(post_process.DropExpectation))

  yield api.test(
      'build_system_siso_to_ninja',
      api.path.exists(api.path['checkout'].join('out', 'Release',
                                                'LAST_BUILD_SYSTEM.txt')),
      api.step_data('read LAST_BUILD_SYSTEM.txt', api.file.read_text('siso')),
      api.post_check(post_process.MustRun, 'remove unreliable output dir'),
      api.post_process(post_process.DropExpectation))
