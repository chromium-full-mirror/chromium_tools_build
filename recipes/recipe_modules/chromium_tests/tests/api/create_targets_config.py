# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'recipe_engine/assertions',
    'recipe_engine/platform',
    'recipe_engine/properties',
]

PROPERTIES = {
    'isolated_tests_only': Property(default=False),
    'expected_tests': Property(default=[]),
    'targets_spec_dir': Property(default=None),
}

FAKE_TARGETS_SPEC = {
    'junit_tests': [{
        'name':
            'android_webview_junit_tests',
        'swarming': {},
        'test':
            'android_webview_junit_tests',
        'test_id_prefix':
            'ninja://android_webview/test:android_webview_junit_tests/'
    }],
    'scripts': [{
        'isolate_profile_data': True,
        'name': 'check_static_initializers',
        'script': 'check_static_initializers.py',
        'swarming': {}
    }],
    'isolated_scripts': [{
        'isolate_name': 'angle_unittests',
        'name': 'angle_unittests',
        'swarming': {
            'can_use_on_swarming_builders': True,
        }
    }, {
        'isolate_name': 'angle_unittests_no_swarm',
        'name': 'angle_unittests_no_swarm',
        'swarming': {
            'can_use_on_swarming_builders': False,
        },
    }],
    'gtest_tests': [
        {
            'name': 'browser_tests',
            'swarming': {
                'can_use_on_swarming_builders': True,
            },
            'isolate_coverage_data': True,
        },
        {
            'name': 'browser_tests_no_swarm',
            'swarming': {
                'can_use_on_swarming_builders': False,
            },
            'isolate_coverage_data': True,
        },
    ],
}


def RunSteps(api, isolated_tests_only, expected_tests, targets_spec_dir):
  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  api.chromium_tests.configure_build(builder_config)
  targets_config = api.chromium_tests.create_targets_config(
      builder_config, {
          "got_angle_revision": "19582d1201aab222b61be3858776e6fe93967895",
          "got_nacl_revision": "f231a6e8c08f6733c072ae9cca3ce00f42edd9ff",
          "got_revision": "549c1631e57b7f1980d1f3529a7ac8a9b41e92a3",
          "got_revision_cp": "refs/heads/main@{#992167}",
          "got_v8_revision": "e3fd28e1be7019002f0e07c8aaf0bc21d78bd294",
          "got_v8_revision_cp": "refs/heads/10.2.145@{#1}",
          "got_webrtc_revision": "a19f0c7409f1dc4316bb6a6d9a97d3261539a84d",
          "got_webrtc_revision_cp": "refs/heads/main@{#36539}",
      },
      api.chromium_checkout.src_dir,
      targets_spec_dir=targets_spec_dir,
      isolated_tests_only=isolated_tests_only)
  tests = []
  for t in targets_config.all_tests:
    tests.append(t.name)

  api.assertions.assertCountEqual(tests, expected_tests)


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  def fake_targets_spec():
    return api.chromium_tests.read_targets_spec(
        'fake-group',
        {'fake-builder': FAKE_TARGETS_SPEC},
    )

  def ctbc_properties():
    return ctbc_api.properties(
        ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
            builder_group='fake-group',
            builder='fake-builder',
        ).with_mirrored_tester(
            builder_group='fake-group',
            builder='fake-tester',
        ).assemble())

  yield api.test(
      'basic',
      ctbc_properties(),
      api.properties(
          isolated_tests_only=False,
          expected_tests=[
              'angle_unittests', 'angle_unittests_no_swarm', 'browser_tests',
              'browser_tests_no_swarm', 'android_webview_junit_tests',
              'check_static_initializers'
          ],
      ),
      fake_targets_spec(),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      api.post_process(
          post_process.StepTextContains,
          'read test spec (fake-group.json)',
          ['testing/buildbot'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'targets_spec_dir',
      ctbc_properties(),
      api.properties(
          isolated_tests_only=False,
          expected_tests=[
              'angle_unittests', 'angle_unittests_no_swarm', 'browser_tests',
              'browser_tests_no_swarm', 'android_webview_junit_tests',
              'check_static_initializers'
          ],
          targets_spec_dir=api.chromium_checkout.src_dir.join('infra/specs'),
      ),
      fake_targets_spec(),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      api.post_process(
          post_process.StepTextContains,
          'read test spec (fake-group.json)',
          ['infra/specs'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'isolated_tests_only',
      ctbc_properties(),
      api.properties(
          isolated_tests_only=True,
          expected_tests=[
              'angle_unittests', 'angle_unittests_no_swarm', 'browser_tests'
          ],
      ),
      fake_targets_spec(),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      api.post_process(post_process.DropExpectation),
  )
