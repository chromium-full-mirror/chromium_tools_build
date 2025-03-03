# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium_tests',
    'chromium_tests_builder_config',
    'filter',
    'depot_tools/tryserver',
    'recipe_engine/json',
]

def RunSteps(api):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  if api.tryserver.is_tryserver:
    return api.chromium_tests.trybot_steps(builder_id, builder_config)
  build_result, _ = api.chromium_tests.main_waterfall_steps(
      builder_id, builder_config)
  return build_result


def GenTests(api):
  builder_db = ctbc.BuilderDatabase.create({
      'test-group': {
          'test-builder':
              ctbc.BuilderSpec.create(
                  chromium_config='chromium',
                  gclient_config='chromium',
              ),
      }
  })
  try_db = ctbc.TryDatabase.create({
      'test-try-group': {
          'test-try-builder':
              ctbc.TrySpec.create_for_single_mirror(
                  builder_group='test-group',
                  buildername='test-builder',
              ),
      }
  })

  def common_test_data(test_spec):
    return api.chromium_tests.read_targets_spec('test-group', {
        'test-builder': {
            'scripts': [test_spec],
        },
    })

  def ci_build(test_spec, **kwargs):
    t = api.chromium_tests_builder_config.ci_build(
        builder_group='test-group',
        builder='test-builder',
        builder_db=builder_db,
        **kwargs)
    t += common_test_data(test_spec)
    return t

  def try_build(test_spec, **kwargs):
    t = api.chromium_tests_builder_config.try_build(
        builder_group='test-group',
        builder='test-builder',
        builder_db=builder_db,
        try_db=try_db,
        **kwargs)
    t += common_test_data(test_spec)
    return t

  yield api.test(
      'basic',
      ci_build(test_spec={
          'name': 'base_unittests',
          'script': 'gtest_test.py',
      }),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          'vpython3',
          '[CACHE]/builder/src/testing/scripts/gtest_test.py',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci-with-args',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'script': 'gtest_test.py',
              'args': ['common-arg'],
              'precommit_args': ['try-arg'],
              'non_precommit_args': ['ci-arg'],
          }),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          '--args',
          api.json.dumps(['common-arg', 'ci-arg']),
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'try-with-args',
      try_build(
          test_spec={
              'name': 'base_unittests',
              'script': 'gtest_test.py',
              'args': ['common-arg'],
              'precommit_args': ['try-arg'],
              'non_precommit_args': ['ci-arg'],
          }),
      api.post_process(
          post_process.StepCommandContains,
          'base_unittests (with patch)',
          [
              '--args',
              api.json.dumps(['common-arg', 'try-arg']),
          ],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test_suite_with_decription_on_tryserver',
      try_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'gtest_test',
              'script': 'gtest_test.py',
              'description': 'This is a description.'
          }),
      api.post_process(post_process.StepTextContains,
                       'base_unittests (with patch)',
                       ['This is a description.']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'test_suite_with_decription_on_ci_builder',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'gtest_test',
              'script': 'gtest_test.py',
              'description': 'This is a description.'
          }),
      api.post_process(post_process.StepTextContains, 'base_unittests',
                       ['This is a description.']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_on_ci_builder',
      ci_build(test_spec={
          'name': 'base_unittests',
          'ci_only': True,
          'script': 'gtest_test.py',
      }),
      api.post_process(post_process.MustRun, 'base_unittests'),
      api.post_process(post_process.StepTextContains, 'base_unittests',
                       ['This test will not be run on try builders']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_on_try_builder',
      try_build(test_spec={
          'name': 'base_unittests',
          'ci_only': True,
          'script': 'gtest_test.py',
      }),
      api.post_process(post_process.StepCommandEmpty,
                       'base_unittests (with patch)'),
      api.post_process(
          post_process.StepTextContains,
          'base_unittests (with patch)',
          ["This test is not being run because it is marked 'ci_only'"],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only_on_try_builder_bypass',
      try_build(test_spec={
          'name': 'base_unittests',
          'ci_only': True,
          'script': 'gtest_test.py',
      }),
      api.step_data('parse description',
                    api.json.output({'Include-Ci-Only-Tests': ['true']})),
      api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
      api.post_process(post_process.StepTextContains,
                       'base_unittests (with patch)',
                       [('This test is being run due to the'
                         ' Include-Ci-Only-Tests gerrit footer')]),
      api.post_process(post_process.DropExpectation),
  )
