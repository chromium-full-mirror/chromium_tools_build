# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test cases for common generation code.

The functionality for handling descriptions and TestWrappers is independent of
the test type, so this saves us from having to add test cases for the common
generator functionality to each of the generate_* tests or risk having coverage
for the wrappers scattered about.
"""

from recipe_engine import post_process

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/cv',
    'recipe_engine/json',
]


def RunSteps(api):
  api.chromium_swarming.set_default_dimension('os', 'Linux')
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  if api.tryserver.is_tryserver:
    return api.chromium_tests.trybot_steps(builder_id, builder_config)
  build_result, _ = api.chromium_tests.main_waterfall_steps(
      builder_id, builder_config)
  return build_result


def GenTests(api):

  def common_test_data(test_spec):
    return api.chromium_tests.read_targets_spec('fake-group', {
        'fake-builder': {
            'isolated_scripts': [test_spec],
        },
    })

  ctbc_api = api.chromium_tests_builder_config

  def ci_build(test_spec, **kwargs):
    t = api.chromium.ci_build(
        builder_group='fake-group', builder='fake-builder', **kwargs)
    t += ctbc_api.properties(
        ctbc_api.properties_assembler_for_ci_builder(
            builder_group='fake-group',
            builder='fake-builder',
        ).assemble())
    t += common_test_data(test_spec)
    return t

  def try_build(test_spec, **kwargs):
    t = api.chromium.try_build(
        builder_group='fake-try-group', builder='fake-try-builder', **kwargs)
    t += ctbc_api.properties(
        ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
            builder_group='fake-group',
            builder='fake-builder',
        ).assemble())
    t += common_test_data(test_spec)
    return t

  yield api.test(
      'description',
      ci_build(test_spec={
          'name': 'fake-test',
          'description': 'This is a description.',
      }),
      api.post_process(post_process.StepTextContains, 'fake-test',
                       ['This is a description.']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experimental',
      ci_build(test_spec={
          'name': 'fake-test',
          'experiment_percentage': '100',
      }),
      api.step_data('fake-test (experimental)', retcode=1),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experimental-off',
      ci_build(test_spec={
          'name': 'fake-test',
          'experiment_percentage': '0',
      }),
      api.post_process(post_process.StepCommandEmpty,
                       'fake-test (experimental)'),
      api.post_process(
          post_process.StepTextContains,
          'fake-test (experimental)',
          ['This test was not selected for its experiment in this build'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only-ci',
      ci_build(test_spec={
          'name': 'fake-test',
          'ci_only': True,
      }),
      api.post_process(post_process.StepTextContains, 'fake-test',
                       ['This test will not be run on try builders']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only-try',
      try_build(test_spec={
          'name': 'fake-test',
          'ci_only': True,
      }),
      api.post_process(post_process.StepCommandEmpty, 'fake-test (with patch)'),
      api.post_process(
          post_process.StepTextContains,
          'fake-test (with patch)',
          [("This test is not being run because it is marked 'ci_only'. "
            "Use 'Include-Ci-Only-Tests: true' to override.")],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only-try-with-bypass',
      try_build(test_spec={
          'name': 'fake-test',
          'ci_only': True,
      }),
      api.step_data('parse description',
                    api.json.output({'Include-Ci-Only-Tests': ['true']})),
      api.post_process(post_process.MustRun, 'fake-test (with patch)'),
      api.post_process(post_process.StepTextContains, 'fake-test (with patch)',
                       [('This test is being run due to the'
                         ' Include-Ci-Only-Tests gerrit footer')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ci_only-mega-cq',
      try_build(
          test_spec={
              'test': 'fake-test',
              'ci_only': True,
          },
          tags=api.buildbucket.tags(
              cq_equivalent_cl_group_key='12345', cq_attempt_key='67890'),
      ),
      api.cv(run_mode='CQ_MODE_MEGA_DRY_RUN'),
      api.post_process(post_process.MustRun, 'fake-test (with patch)'),
      api.post_process(post_process.StepTextContains, 'fake-test (with patch)',
                       [('This test is being run on Mega CQ runs')]),
      api.post_process(post_process.DropExpectation),
  )
