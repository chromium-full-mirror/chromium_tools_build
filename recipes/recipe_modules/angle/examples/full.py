# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.chromium_tests_builder_config import (builder_db,
                                                                builder_spec,
                                                                try_spec)

DEPS = [
    'angle',
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/legacy_annotation',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/path',
    'recipe_engine/raw_io',
    'siso',
    'test_utils',
]


def RunSteps(api):
  return api.angle.steps()


_TEST_BUILDERS = builder_db.BuilderDatabase.create({
    'angle': {
        'android-builder':
            builder_spec.BuilderSpec.create(
                gclient_config='angle_android', chromium_config='angle_clang'),
        'linux-clang-builder':
            builder_spec.BuilderSpec.create(
                gclient_config='angle_mesa', chromium_config='angle_clang'),
        'linux-gcc-builder':
            builder_spec.BuilderSpec.create(
                gclient_config='angle', chromium_config='angle_non_clang'),
        'mac-builder':
            builder_spec.BuilderSpec.create(
                gclient_config='angle', chromium_config='angle_clang'),
        'win-clang-builder':
            builder_spec.BuilderSpec.create(
                gclient_config='angle', chromium_config='angle_clang'),
        'win-msvc-builder':
            builder_spec.BuilderSpec.create(
                gclient_config='angle', chromium_config='angle_non_clang'),
    },
})

_TEST_TRYBOTS = try_spec.TryDatabase.create({
    'angle': {
        'android-builder':
            try_spec.TrySpec.create(
                mirrors=[
                    try_spec.TryMirror.create(
                        builder_group='angle',
                        buildername='android-builder',
                    ),
                ],),
        'linux-clang-builder':
            try_spec.TrySpec.create(
                mirrors=[
                    try_spec.TryMirror.create(
                        builder_group='angle',
                        buildername='linux-clang-builder',
                    ),
                ],),
        'linux-gcc-builder':
            try_spec.TrySpec.create(
                mirrors=[
                    try_spec.TryMirror.create(
                        builder_group='angle',
                        buildername='linux-gcc-builder',
                    ),
                ],),
        'mac-builder':
            try_spec.TrySpec.create(
                mirrors=[
                    try_spec.TryMirror.create(
                        builder_group='angle',
                        buildername='mac-builder',
                    ),
                ],),
        'win-clang-builder':
            try_spec.TrySpec.create(
                mirrors=[
                    try_spec.TryMirror.create(
                        builder_group='angle',
                        buildername='win-clang-builder',
                    ),
                ],),
        'win-msvc-builder':
            try_spec.TrySpec.create(
                mirrors=[
                    try_spec.TryMirror.create(
                        builder_group='angle',
                        buildername='win-msvc-builder',
                    ),
                ],),
    }
})


def GenTests(api):

  def ci_build(builder,
               toolchain='clang',
               platform='linux',
               test_mode='compile_and_test'):
    return api.angle.ci_build(
        builder=builder,
        bot_id='build1-a1',
        build_number=1234,
        toolchain=toolchain,
        platform=platform,
        test_mode=test_mode,
    ) + api.angle.builders(_TEST_BUILDERS) + api.angle.trybots(
        _TEST_TRYBOTS) + api.angle.override_commit_pos_data(
        ) + api.siso.properties()

  yield api.test(
      'android_test',
      ci_build('android-builder', platform='android', test_mode='compile_only'),
  )
  yield api.test(
      'basic_test',
      ci_build('linux-clang-builder'),
  )
  yield api.test(
      'basic_test_remoteexec',
      ci_build('linux-clang-builder'),
      api.siso.properties(),
  )
  yield api.test(
      'basic_mac_test',
      api.platform('mac', 64),
      ci_build('mac-builder', platform='mac', test_mode='compile_only'),
  )
  yield api.test(
      'compile_only_compile_failed_test',
      ci_build('linux-clang-builder', test_mode='compile_only'),
      api.step_data('compile', api.legacy_annotation.infra_failure_step),
      api.expect_status('FAILURE'),
  )
  yield api.test(
      'compile_and_test_compile_failed_test',
      ci_build('linux-clang-builder'),
      api.step_data('compile', api.legacy_annotation.infra_failure_step),
      api.expect_status('FAILURE'),
  )
  yield api.test(
      'win_non_clang_test',
      api.platform('win', 64),
      ci_build(
          builder='win-msvc-builder',
          toolchain='msvc',
          platform='win',
          test_mode='compile_only'),
  )
  yield api.test(
      'linux_non_clang_test',
      api.platform('linux', 64),
      ci_build(
          builder='linux-gcc-builder',
          toolchain='gcc',
          test_mode='checkout_only'),
  )
  yield api.test(
      'linux_trace_test',
      api.platform('linux', 64),
      ci_build(builder='linux-clang-builder', test_mode='trace_tests'),
  )
  yield api.test(
      'win_trace_test',
      api.platform('win', 64),
      ci_build(
          builder='win-clang-builder', platform='win', test_mode='trace_tests'),
  )
  yield api.test(
      'win_trace_test_failure',
      api.platform('win', 64),
      ci_build(
          builder='win-clang-builder', platform='win', test_mode='trace_tests'),
      api.step_data('GLES 2.0 trace tests',
                    api.legacy_annotation.infra_failure_step),
      api.expect_status('FAILURE'),
  )
  yield api.test(
      'invalid_json_test',
      ci_build('linux-clang-builder'),
      api.chromium_tests.read_targets_spec(
          'angle', {
              'linux-clang-builder': {
                  'isolated_scripts': [{
                      'test': 'basic_isolate',
                      'name': 'basic_isolate_tests',
                  },],
              },
          }),
      api.override_step_data('basic_isolate_tests', retcode=1),
      api.expect_status('FAILURE'),
  )
  yield api.test(
      'failed_json_test',
      ci_build('linux-clang-builder'),
      api.chromium_tests.read_targets_spec(
          'angle', {
              'linux-clang-builder': {
                  'isolated_scripts': [{
                      'test': 'basic_isolate',
                      'name': 'basic_isolate_tests',
                  },],
              },
          }),
      api.override_step_data(
          'basic_isolate_tests results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'basic_isolate_tests', failing_tests=['Test.One']))),
      api.expect_status('FAILURE'),
  )
