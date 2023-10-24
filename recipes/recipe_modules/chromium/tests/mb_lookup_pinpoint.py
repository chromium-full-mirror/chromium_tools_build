# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium

DEPS = [
    'chromium',
    'recipe_engine/assertions',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
]


def RunSteps(api):
  api.chromium.set_config(
      api.properties.get('chromium_config', 'chromium'),
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'),
      TARGET_CROS_BOARDS=api.properties.get('target_cros_boards'))

  gn_args = api.chromium.mb_lookup(
      chromium.BuilderId.create_for_group('chromium.perf.pinpoint',
                                          'linux-perf'),
      recursive=api.properties.get('recursive', False),
      use_goma=False,
      use_reclient=True)
  expected_gn_args = api.properties.get('expected_gn_args')
  api.assertions.assertEqual(gn_args, expected_gn_args)


def GenTests(api):
  gn_args = '\n'.join((
      'target_cpu = "x86"',
      'target_sysroot = "//build/linux"',
      'use_remoteexec = true',
  ))
  expected_step_text = [
      '<br/>'.join(('target_cpu = "x86"', 'use_remoteexec = true')),
      'target_sysroot = "//build/linux"',
  ]

  yield api.test(
      'basic',
      api.properties(expected_gn_args=gn_args),
      api.step_data('lookup GN args', stdout=api.raw_io.output_text(gn_args)),
      api.post_process(post_process.StepTextContains, 'lookup GN args',
                       expected_step_text),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'pinpoint_without_new_conf',
      api.properties(expected_gn_args=gn_args),
      api.step_data('lookup GN args'),
      api.override_step_data('lookup GN args', retcode=1),
      api.step_data(
          'lookup GN args (2)', stdout=api.raw_io.output_text(gn_args)),
      api.post_process(post_process.StepTextContains, 'lookup GN args (2)',
                       expected_step_text),
      api.post_process(post_process.StepCommandContains, 'lookup GN args (2)', [
          '-m',
          'chromium.perf',
      ]),
      api.post_process(post_process.StepTextContains, 'lookup GN args (2)',
                       expected_step_text),
      api.post_process(post_process.DropExpectation),
  )
