# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
This recipe executes typical trybot steps, but runs in CI with CI
properties. It tests the integration of certain sub-projects in
Chromium and gives a preview on test failures that'd happen if
those sub-projects rolled to their current revision.
"""


import attr

from recipe_engine import post_process

DEPS = [
    'chromium',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'filter',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'test_utils',
]


def RunSteps(api):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  if builder_id.builder == 'V8 Blink Linux':
    builder_config = attr.evolve(builder_config, retry_failed_shards=True)
  with api.chromium.chromium_layout():
    return api.chromium_tests.integration_steps(builder_id, builder_config)


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  def ci_props(extra_swarmed_tests=None):
    swarm_hashes = {}
    if extra_swarmed_tests:
      for test in extra_swarmed_tests:
        swarm_hashes[test] = '[dummy hash for %s/size]' % test

    return sum([
        api.properties(swarm_hashes=swarm_hashes),
        api.platform('linux', 64),
        api.chromium.ci_build(
            project='project',
            builder_group='client.v8.fyi',
            builder='V8 Blink Linux',
            git_repo='https://chromium.googlesource.com/v8/v8.git',
        ),
        ctbc_api.properties(
            ctbc_api.properties_assembler_for_ci_builder(
                builder_group='client.v8.fyi',
                builder='V8 Blink Linux').assemble()),
    ], api.empty_test_data())

  def blink_test_setup():
    return (ci_props(extra_swarmed_tests=['blink_web_tests']) +
            api.chromium_tests.read_targets_spec(
                'client.v8.fyi', {
                    'V8 Blink Linux': {
                        'isolated_scripts': [{
                            'test': 'blink_web_tests',
                            'name': 'blink_web_tests',
                            'swarming': {
                                'dimensions': {
                                    'os': 'Linux',
                                },
                            },
                            'results_handler': 'layout tests',
                        },],
                    },
                }))

  def blink_test(succeeds_with_patch,
                 succeeds_without_patch=None,
                 succeeds_retry_with_patch=False):

    def test_result(suffix, is_successful):
      return api.chromium_tests.gen_swarming_and_rdb_results(
          'blink_web_tests',
          suffix,
          failures=[] if is_successful else ['Test.One'])

    result = blink_test_setup()

    result += test_result('with patch', succeeds_with_patch)

    if not succeeds_with_patch:
      result += test_result('retry shards with patch',
                            succeeds_retry_with_patch)

    # "without patch" step is only run if the initial run and the retry both fail.
    if not succeeds_with_patch and not succeeds_retry_with_patch and succeeds_without_patch is not None:
      result += test_result('without patch', succeeds_without_patch)

    return result

  yield api.test(
      'passing',
      blink_test(succeeds_with_patch=True),
      api.post_process(post_process.MustRun, 'blink_web_tests (with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'blink_web_tests (without patch)'),
      api.expect_status('SUCCESS'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'flaky_failure_with_patch',
      blink_test(succeeds_with_patch=False, succeeds_retry_with_patch=True),
      api.post_process(post_process.MustRun, 'blink_web_tests (with patch)'),
      api.post_process(post_process.MustRun,
                       'blink_web_tests (retry shards with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'blink_web_tests (without patch)'),
      api.expect_status('SUCCESS'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'bug_introduced_by_commit',
      blink_test(succeeds_with_patch=False, succeeds_without_patch=True),
      api.post_process(post_process.MustRun, 'blink_web_tests (with patch)'),
      api.post_process(post_process.MustRun,
                       'blink_web_tests (retry shards with patch)'),
      api.post_process(post_process.MustRun, 'blink_web_tests (without patch)'),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'bug_introduced_by_chromium',
      blink_test(succeeds_with_patch=False, succeeds_without_patch=False),
      api.post_process(post_process.MustRun, 'blink_web_tests (with patch)'),
      api.post_process(post_process.MustRun,
                       'blink_web_tests (retry shards with patch)'),
      api.post_process(post_process.MustRun, 'blink_web_tests (without patch)'),
      api.expect_status('SUCCESS'),
  )
