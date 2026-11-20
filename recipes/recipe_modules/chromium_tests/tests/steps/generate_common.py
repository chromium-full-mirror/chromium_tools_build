# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test cases for common generation code.

The functionality for handling descriptions and TestWrappers is independent of
the test type, so this saves us from having to add test cases for the common
generator functionality to each of the generate_* tests or risk having coverage
for the wrappers scattered about.
"""

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import buildbucket, cv, json, raw_io


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  cv: cv.API
  json: json.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  cv: cv.TEST_API
  json: json.TEST_API
  raw_io: raw_io.TEST_API


def RunSteps(api: DEPS):
  api.chromium_swarming.set_default_dimension('os', 'Linux')
  builder_id, builder_config = (
    api.chromium_tests_builder_config.lookup_builder()
  )
  if api.tryserver.is_tryserver:
    return api.chromium_tests.trybot_steps(builder_id, builder_config)
  build_result, _ = api.chromium_tests.main_waterfall_steps(
    builder_id, builder_config
  )
  return build_result


def GenTests(api: TEST_DEPS):

  def common_test_data(test_spec):
    return api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [test_spec],
        },
      },
    )

  ctbc_api = api.chromium_tests_builder_config

  def ci_build(test_spec, **kwargs):
    t = api.chromium.ci_build(
      builder_group='fake-group', builder='fake-builder', **kwargs
    )
    t += ctbc_api.properties(
      ctbc_api.properties_assembler_for_ci_builder(
        builder_group='fake-group',
        builder='fake-builder',
      ).assemble()
    )
    t += common_test_data(test_spec)
    return t

  def try_build(test_spec, **kwargs):
    t = api.chromium.try_build(
      builder_group='fake-try-group', builder='fake-try-builder', **kwargs
    )
    t += ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    )
    t += common_test_data(test_spec)
    return t

  yield api.test(
    'description',
    ci_build(
      test_spec={
        'name': 'fake-test',
        'description': 'This is a description.',
      }
    ),
    api.post_process(
      post_process.StepTextContains, 'fake-test', ['This is a description.']
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'experimental',
    ci_build(
      test_spec={
        'name': 'fake-test',
        'experiment_percentage': '100',
      }
    ),
    api.step_data('fake-test (experimental)', retcode=1),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'experimental-off',
    ci_build(
      test_spec={
        'name': 'fake-test',
        'experiment_percentage': '0',
      }
    ),
    api.post_process(post_process.StepCommandEmpty, 'fake-test (experimental)'),
    api.post_process(
      post_process.StepTextContains,
      'fake-test (experimental)',
      ['This test was not selected for its experiment in this build'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ci_only-ci',
    ci_build(
      test_spec={
        'name': 'fake-test',
        'ci_only': True,
      }
    ),
    api.post_process(
      post_process.StepTextContains,
      'fake-test',
      ['This test will not be run on try builders'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ci_only-try',
    try_build(
      test_spec={
        'name': 'fake-test',
        'ci_only': True,
      }
    ),
    api.post_process(post_process.StepCommandEmpty, 'fake-test (with patch)'),
    api.post_process(
      post_process.StepTextContains,
      'fake-test (with patch)',
      [
        (
          "This test is not being run because it is marked 'ci_only'."
          " Add 'Include-Ci-Only-Tests: fake-group:fake-builder|fake-test'"
          " to CL footers to override."
        )
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ci_only-try-with-global-bypass',
    try_build(
      test_spec={
        'name': 'fake-test',
        'ci_only': True,
      }
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'name': 'fake-test',
              'ci_only': True,
              'swarming': {'can_use_on_swarming_builders': True},
            },
          ],
        },
      },
    ),
    api.step_data(
      'parse description', api.json.output({'Include-Ci-Only-Tests': ['true']})
    ),
    api.post_process(post_process.MustRun, 'fake-test (with patch)'),
    api.post_process(
      post_process.StepTextContains,
      'fake-test (with patch)',
      [
        (
          'This test is being run due to the'
          ' Include-Ci-Only-Tests gerrit footer'
        )
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ci_only-try-with-builder-specific-test-wildcard-bypass',
    api.chromium.try_build(
      builder_group='fake-try-group', builder='fake-try-builder'
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              'name': 'fake-test',
              'ci_only': True,
            },
            {
              'name': 'fake-test2',
              'ci_only': True,
            },
          ],
        },
      },
    ),
    api.step_data(
      'parse description',
      api.json.output(
        {
          'Include-Ci-Only-Tests': [
            (
              'other-group:other-builder,fake-group:fake-builder,'
              'other-group:other-builder2|*'
            )
          ]
        }
      ),
    ),
    api.post_process(
      post_process.StepTextContains,
      'fake-test (with patch)',
      [
        (
          'This test is being run due to the'
          ' Include-Ci-Only-Tests gerrit footer'
        )
      ],
    ),
    api.post_process(
      post_process.StepTextContains,
      'fake-test2 (with patch)',
      [
        (
          'This test is being run due to the'
          ' Include-Ci-Only-Tests gerrit footer'
        )
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ci_only-try-with-builder-wildcard-test-specific-bypass',
    api.chromium.try_build(
      builder_group='fake-try-group', builder='fake-try-builder'
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              'name': 'fake-test',
              'ci_only': True,
            },
            {
              'name': 'fake-test2',
              'ci_only': True,
            },
          ],
        },
      },
    ),
    api.step_data(
      'parse description',
      api.json.output(
        {'Include-Ci-Only-Tests': ['*|other-test,fake-test,other-test2']}
      ),
    ),
    api.post_process(
      post_process.StepTextContains,
      'fake-test (with patch)',
      [
        (
          'This test is being run due to the'
          ' Include-Ci-Only-Tests gerrit footer'
        )
      ],
    ),
    api.post_process(post_process.StepCommandEmpty, 'fake-test2 (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ci_only-try-with-non-matching-bypass',
    api.chromium.try_build(
      builder_group='fake-try-group', builder='fake-try-builder'
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              'name': 'fake-test',
              'ci_only': True,
            }
          ],
        },
      },
    ),
    api.step_data(
      'parse description',
      api.json.output(
        {'Include-Ci-Only-Tests': ['other-group:other-builder|fake-test']}
      ),
    ),
    api.post_process(post_process.StepCommandEmpty, 'fake-test (with patch)'),
    api.post_process(
      post_process.StepTextContains,
      'fake-test (with patch)',
      [
        (
          "This test is not being run because it is marked 'ci_only'."
          " Add 'Include-Ci-Only-Tests: fake-group:fake-builder|fake-test'"
          " to CL footers to override."
        )
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ci_only-bad-bypass',
    api.chromium.try_build(
      builder_group='fake-try-group', builder='fake-try-builder'
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              'name': 'fake-test',
              'ci_only': True,
            }
          ],
        },
      },
    ),
    api.step_data(
      'parse description',
      api.json.output({'Include-Ci-Only-Tests': ['bad-footer']}),
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.SummaryMarkdownRE,
      "invalid format for Include-Ci-Only-Tests footer: 'bad-footer'",
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ci_only-bad-builder-in-bypass',
    api.chromium.try_build(
      builder_group='fake-try-group', builder='fake-try-builder'
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              'name': 'fake-test',
              'ci_only': True,
            }
          ],
        },
      },
    ),
    api.step_data(
      'parse description',
      api.json.output({'Include-Ci-Only-Tests': ['bad-builder|fake-test']}),
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.SummaryMarkdownRE,
      (
        "invalid format for builder 'bad-builder'"
        ' in Include-Ci-Only-Tests footer'
      ),
    ),
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
        cq_equivalent_cl_group_key='12345', cq_attempt_key='67890'
      ),
    ),
    api.cv(run_mode='CQ_MODE_MEGA_DRY_RUN'),
    api.post_process(post_process.MustRun, 'fake-test (with patch)'),
    api.post_process(
      post_process.StepTextContains,
      'fake-test (with patch)',
      [('This test is being run on Mega CQ runs')],
    ),
    api.post_process(post_process.DropExpectation),
  )

  location_filters = [r'third_party/chromium-bidi/.+', r'content/.+\.cc']

  def try_build_with_other_test(test_spec):
    # The build stops before the tests if there are no targets to compile. The
    # other test makes sure that the build shows the step of a disabled test.
    return (
      api.chromium.try_build(
        builder_group='fake-try-group', builder='fake-try-builder'
      )
      + ctbc_api.properties(
        ctbc_api.properties_assembler_for_try_builder()
        .with_mirrored_builder(
          builder_group='fake-group',
          builder='fake-builder',
        )
        .assemble()
      )
      + api.chromium_tests.read_targets_spec(
        'fake-group',
        {
          'fake-builder': {
            'isolated_scripts': [test_spec, {'name': 'other-test'}],
          },
        },
      )
    )

  def step_command_omits(check, steps, step_name, target):
    check(target not in steps[step_name].cmd)

  def step_has_command(check, steps, step_name):
    check(steps[step_name].cmd)

  yield api.test(
    'location_filters-ci',
    ci_build(
      test_spec={
        'name': 'fake-test',
        'location_filters': location_filters,
      }
    ),
    api.post_process(post_process.MustRun, 'fake-test'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'location_filters-try-no-match',
    try_build_with_other_test(
      test_spec={
        'name': 'fake-test',
        'location_filters': location_filters,
      }
    ),
    api.override_step_data(
      'git diff to analyze patch',
      api.raw_io.stream_output('chrome/browser/flag-metadata.json'),
    ),
    api.post_process(post_process.StepCommandEmpty, 'fake-test (with patch)'),
    api.post_process(
      post_process.StepTextContains,
      'fake-test (with patch)',
      [
        (
          'This test is not being run because no file affected by the CL'
          ' matches its location_filters:'
          '<ul><li>third_party/chromium-bidi/.+</li>'
          '<li>content/.+\\.cc</li></ul>'
          'Add the following CL footer to override: <code>'
          'Include-Ci-Only-Tests: fake-group:fake-builder|fake-test</code>'
        )
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'location_filters-try-match',
    try_build(
      test_spec={
        'name': 'fake-test',
        'location_filters': location_filters,
      }
    ),
    api.override_step_data(
      'git diff to analyze patch',
      api.raw_io.stream_output(
        'chrome/browser/flag-metadata.json\n'
        'third_party/chromium-bidi/BUILD.gn\n'
      ),
    ),
    api.post_process(post_process.MustRun, 'fake-test (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'location_filters-try-partial-match-is-not-a-match',
    try_build_with_other_test(
      test_spec={
        'name': 'fake-test',
        'location_filters': location_filters,
      }
    ),
    # Filters must fully match: content/foo.h does not match content/.+\.cc.
    api.override_step_data(
      'git diff to analyze patch',
      api.raw_io.stream_output('content/foo.h'),
    ),
    api.post_process(post_process.StepCommandEmpty, 'fake-test (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'location_filters-mega-cq',
    try_build(
      test_spec={
        'name': 'fake-test',
        'location_filters': location_filters,
      },
      tags=api.buildbucket.tags(
        cq_equivalent_cl_group_key='12345', cq_attempt_key='67890'
      ),
    ),
    api.cv(run_mode='CQ_MODE_MEGA_DRY_RUN'),
    api.override_step_data(
      'git diff to analyze patch',
      api.raw_io.stream_output('chrome/browser/flag-metadata.json'),
    ),
    api.post_process(post_process.MustRun, 'fake-test (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'location_filters-and-ci_only-try-match',
    try_build(
      test_spec={
        'name': 'fake-test',
        'ci_only': True,
        'location_filters': location_filters,
      }
    ),
    api.override_step_data(
      'git diff to analyze patch',
      api.raw_io.stream_output('third_party/chromium-bidi/BUILD.gn'),
    ),
    # The location filter matches, but ci_only still disables the test.
    api.post_process(post_process.StepCommandEmpty, 'fake-test (with patch)'),
    api.post_process(
      post_process.StepTextContains,
      'fake-test (with patch)',
      ["This test is not being run because it is marked 'ci_only'."],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'location_filters-try-no-match-with-footer',
    try_build(
      test_spec={
        'name': 'fake-test',
        'location_filters': location_filters,
      }
    ),
    api.step_data(
      'parse description',
      api.json.output(
        {'Include-Ci-Only-Tests': ['fake-group:fake-builder|fake-test']}
      ),
    ),
    api.override_step_data(
      'git diff to analyze patch',
      api.raw_io.stream_output('chrome/browser/flag-metadata.json'),
    ),
    # The step has a command, thus the test is run.
    api.post_process(step_has_command, 'fake-test (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'location_filters-try-no-match-with-footer-for-other-test',
    try_build_with_other_test(
      test_spec={
        'name': 'fake-test',
        'location_filters': location_filters,
      }
    ),
    api.step_data(
      'parse description',
      api.json.output(
        {'Include-Ci-Only-Tests': ['fake-group:fake-builder|other-test']}
      ),
    ),
    api.override_step_data(
      'git diff to analyze patch',
      api.raw_io.stream_output('chrome/browser/flag-metadata.json'),
    ),
    api.post_process(post_process.StepCommandEmpty, 'fake-test (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  def analyze_input_omits(check, steps, target):
    check(not any(target in str(arg) for arg in steps['analyze'].cmd))

  # A disabled test is not passed to analyze, also not as a compile-only
  # target.
  yield api.test(
    'location_filters-try-no-match-is-not-analyzed',
    try_build(
      test_spec={
        'name': 'fake-test',
        'location_filters': location_filters,
      }
    ),
    api.override_step_data(
      'git diff to analyze patch',
      api.raw_io.stream_output('chrome/browser/flag-metadata.json'),
    ),
    api.post_process(analyze_input_omits, 'fake-test'),
    api.post_process(post_process.DoesNotRun, 'compile (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'location_filters-try-match-is-analyzed',
    try_build(
      test_spec={
        'name': 'fake-test',
        'location_filters': location_filters,
      }
    ),
    api.override_step_data(
      'git diff to analyze patch',
      api.raw_io.stream_output('third_party/chromium-bidi/BUILD.gn'),
    ),
    api.post_process(
      post_process.StepCommandContains, 'compile (with patch)', ['fake-test']
    ),
    api.post_process(post_process.DropExpectation),
  )

  # The CL changes the test spec file, thus analyze is skipped and all targets
  # are compiled. The location filters still apply.
  yield api.test(
    'location_filters-try-no-match-skipped-analyze',
    try_build_with_other_test(
      test_spec={
        'name': 'fake-test',
        'location_filters': location_filters,
      }
    ),
    api.override_step_data(
      'git diff to analyze patch',
      api.raw_io.stream_output('testing/buildbot/fake-group.json'),
    ),
    api.post_process(
      post_process.StepTextContains, 'analyze', ['skipping analyze']
    ),
    api.post_process(
      post_process.StepCommandContains, 'compile (with patch)', ['other-test']
    ),
    api.post_process(step_command_omits, 'compile (with patch)', 'fake-test'),
    api.post_process(post_process.StepCommandEmpty, 'fake-test (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'location_filters-invalid-regexp',
    try_build(
      test_spec={
        'name': 'fake-test',
        'location_filters': ['third_party/(.+'],
      }
    ),
    api.post_process(
      post_process.StepTextContains,
      'test spec format error',
      [
        "invalid regexp 'third_party/(.+' in location_filters"
        " of test 'fake-test'"
      ],
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
