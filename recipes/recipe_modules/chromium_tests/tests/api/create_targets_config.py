# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.chromium_tests import generators
from RECIPE_MODULES.build.chromium_tests.steps import SuccessReuseTest

DEPS = [
    'chromium',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/tryserver',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/cq',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
]

PROPERTIES = {
    'remote_tests_only': Property(default=False),
    'expected_tests': Property(default=[]),
    'targets_spec_dir': Property(default=None),
    'skip_tests': Property(default=[]),
}

FAKE_TARGETS_SPEC = {
    'scripts': [{
        'isolate_profile_data': True,
        'name': 'check_static_initializers',
        'script': 'check_static_initializers.py',
    }],
    'isolated_scripts': [{
        'test': 'angle_unittests',
        'name': 'angle_unittests',
        'swarming': {},
    }, {
        'test': 'angle_unittests_no_swarm',
        'name': 'angle_unittests_no_swarm',
    }],
    'gtest_tests': [
        {
            'name': 'browser_tests',
            'swarming': {},
            'isolate_profile_data': True,
        },
        {
            'name': 'browser_tests_no_swarm',
            'isolate_profile_data': True,
        },
    ],
    'skylab_tests': [{
        'cros_board': 'eve',
        'cros_img': 'eve-release/R89-13631.0.0',
        'name': 'basic_EVE_TOT',
        'autotest_name': 'chromium',
    }],
}


def RunSteps(api, remote_tests_only, expected_tests, targets_spec_dir,
             skip_tests):
  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  api.chromium_tests.configure_build(builder_config)
  update_result = api.chromium_checkout.ensure_checkout()
  build_dir = update_result.checkout_dir / 'src' / 'out' / 'some-build-dir'
  targets_config = api.chromium_tests.create_targets_config(
      builder_config,
      {
          "got_angle_revision": "19582d1201aab222b61be3858776e6fe93967895",
          "got_nacl_revision": "f231a6e8c08f6733c072ae9cca3ce00f42edd9ff",
          "got_revision": "549c1631e57b7f1980d1f3529a7ac8a9b41e92a3",
          "got_revision_cp": "refs/heads/main@{#992167}",
          "got_v8_revision": "e3fd28e1be7019002f0e07c8aaf0bc21d78bd294",
          "got_v8_revision_cp": "refs/heads/10.2.145@{#1}",
          "got_webrtc_revision": "a19f0c7409f1dc4316bb6a6d9a97d3261539a84d",
          "got_webrtc_revision_cp": "refs/heads/main@{#36539}",
      },
      update_result.source_root.path,
      build_dir,
      checkout_dir=update_result.checkout_dir,
      targets_spec_dir=targets_spec_dir,
      precommit_details=(generators.PrecommitDetails()
                         if api.tryserver.is_tryserver else None),
      remote_tests_only=remote_tests_only,
  )
  tests = []
  skipped_tests = []
  for t in targets_config.all_tests:
    tests.append(t.name)
    if isinstance(t, SuccessReuseTest):
      skipped_tests.append(t.name)

  api.assertions.assertCountEqual(tests, expected_tests)
  api.assertions.assertCountEqual(skip_tests, skipped_tests)


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  def fake_targets_spec():
    return api.chromium_tests.read_targets_spec(
        'fake-group',
        {'fake-builder': FAKE_TARGETS_SPEC},
    )

  def ctbc_properties(targets_spec_directory: str | None = None):
    assembler = ctbc_api.properties_assembler_for_try_builder(
    ).with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
    ).with_mirrored_tester(
        builder_group='fake-group',
        builder='fake-tester',
    )
    if targets_spec_directory:
      assembler.with_targets_spec_directory(targets_spec_directory)
    return ctbc_api.properties(assembler.assemble())

  yield api.test(
      'basic',
      ctbc_properties(),
      api.properties(
          remote_tests_only=False,
          expected_tests=[
              'angle_unittests', 'angle_unittests_no_swarm', 'browser_tests',
              'browser_tests_no_swarm', 'check_static_initializers',
              'basic_EVE_TOT'
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
      'builder-config-with-targets-spec-directory',
      ctbc_properties('builder-config/targets'),
      api.properties(
          remote_tests_only=False,
          expected_tests=[
              'angle_unittests', 'angle_unittests_no_swarm', 'browser_tests',
              'browser_tests_no_swarm', 'check_static_initializers',
              'basic_EVE_TOT'
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
          ['[CACHE]/builder/src/builder-config/targets/fake-group.json'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'targets_spec_dir',
      ctbc_properties(),
      api.properties(
          remote_tests_only=False,
          expected_tests=[
              'angle_unittests', 'angle_unittests_no_swarm', 'browser_tests',
              'browser_tests_no_swarm', 'check_static_initializers',
              'basic_EVE_TOT'
          ],
          targets_spec_dir=api.path.cleanup_dir / 'infra/specs',
      ),
      fake_targets_spec(),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      api.post_process(
          post_process.StepCommandContains,
          'read test spec (fake-group.json)',
          ['[CLEANUP]/infra/specs/fake-group.json'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'remote_tests_only',
      ctbc_properties(),
      api.properties(
          remote_tests_only=True,
          expected_tests=['angle_unittests', 'browser_tests', 'basic_EVE_TOT'],
      ),
      fake_targets_spec(),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skipped_test',
      ctbc_properties(),
      api.properties(
          remote_tests_only=False,
          expected_tests=[
              'angle_unittests', 'angle_unittests_no_swarm', 'browser_tests',
              'browser_tests_no_swarm', 'check_static_initializers',
              'basic_EVE_TOT'
          ],
          skip_tests=['browser_tests']),
      api.chromium_tests.simulate_previous_build(
          test_statuses={'browser_tests': 'Success'}),
      fake_targets_spec(),
      api.cq(run_mode='FULL_RUN'),
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          tags=api.buildbucket.tags(
              cq_equivalent_cl_group_key='12345', cq_attempt_key='67890'),
      ),
      api.post_process(post_process.DropExpectation),
  )
