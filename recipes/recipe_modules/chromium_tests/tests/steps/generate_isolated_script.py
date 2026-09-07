# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import common as resultdb_common
from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import test_result as test_result_pb2
from PB.go.chromium.org.luci.analysis.proto.v1 import test_history

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    chromium_swarming,
    chromium_tests,
    chromium_tests_builder_config,
    flakiness,
)
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
    json,
    luci_analysis,
    raw_io,
    resultdb,
    swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  flakiness: flakiness.API
  json: json.API
  luci_analysis: luci_analysis.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  swarming: swarming.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_swarming: chromium_swarming.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  flakiness: flakiness.TEST_API
  json: json.TEST_API
  luci_analysis: luci_analysis.TEST_API
  raw_io: raw_io.TEST_API
  resultdb: resultdb.TEST_API
  swarming: swarming.TEST_API


def RunSteps(api: DEPS):
  api.chromium_swarming.set_default_dimension('os', 'Linux')
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  if api.tryserver.is_tryserver:
    return api.chromium_tests.trybot_steps(builder_id, builder_config)
  build_result, _ = api.chromium_tests.main_waterfall_steps(
      builder_id, builder_config)
  return build_result


def GenTests(api: TEST_DEPS):
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
            'isolated_scripts': [test_spec],
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

  def test_spec_format_error(s, step_name='test spec format error'):
    """Adds a post check for step named `step_name`.

    The prose contained in the details log must contain the substring
    `s`.
    """

    def check(check, steps):
      details = steps[step_name].logs['details']
      details = details.replace('\n', ' ')
      check(s in details)

    return api.post_check(check)

  yield api.test(
      'basic',
      ci_build(test_spec={
          'name': 'base_unittests',
          'test': 'base_unittests_run',
      }),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          'vpython3',
          '[START_DIR]/swarming.client/run_isolated.py',
      ]),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          '--isolated-script-test-output',
          '[CLEANUP]/tmp_tmp_1',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'fake_results_handler',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'base_unittests_run',
              'results_handler': 'fake',
              'swarming': {},
          }),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'base_unittests_run',
              'test_id_prefix': 'ninja://chrome/test:base_unittests/',
              'merge': {
                  'script': '//path/to/script.py',
              },
              'swarming': {},
          }),
      api.post_process(post_process.MustRun,
                       'test_pre_run.[trigger] base_unittests'),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          '--merge-script',
          '[CACHE]/builder/src/path/to/script.py',
          '--merge-script-stdout-file',
          '/path/to/tmp/merge_script_log',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'service_account',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'base_unittests_run',
              'swarming': {
                  'service_account': 'test-account@serviceaccount.com',
              },
          }),
      api.post_check(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] base_unittests', lambda check, req: check(
              req.service_account == 'test-account@serviceaccount.com')),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_trigger_script',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'base_unittests_run',
              'trigger_script': {
                  'script': '//path/to/script.py',
              },
              'swarming': {},
          }),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.[trigger (custom trigger script)] base_unittests', [
              'vpython3',
              '[CACHE]/builder/src/path/to/script.py',
              'trigger',
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_trigger_script_invalid',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'base_unittests_run',
              'trigger_script': {
                  'script': 'bad',
              },
              'swarming': {},
          }),
      test_spec_format_error('contains a custom trigger_script "bad"'
                             " that doesn't match the expected format"),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'swarming_dimensions',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'base_unittests_run',
              'swarming': {
                  'dimensions': {
                      'os': 'Linux',
                      'foo': None,
                  },
                  'optional_dimensions': {
                      '60': {
                          'bar': 'baz',
                      },
                  },
              },
          }),
      api.post_process(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] base_unittests',
          lambda check, req: check(
              all(('os', 'Linux') in slice.dimensions.items()
                  for slice in req)),
          lambda check, req: check(not any('foo' in slice.dimensions
                                           for slice in req)),
          lambda check, req: check(('bar', 'baz') in req[0].dimensions.items()),
          lambda check, req: check(not any('bar' in slice.dimensions
                                           for slice in req[1:])),
      ),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          'swarming',
          'collect',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_legacy_optional_dimensions',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'base_unittests_run',
              'swarming': {
                  'optional_dimensions': {
                      '60': [{
                          'bar': 'baz',
                      }],
                  },
              },
          }),
      api.post_process(
          api.swarming.check_triggered_request,
          'test_pre_run.[trigger] base_unittests',
          lambda check, req: check(('bar', 'baz') in req[0].dimensions.items()),
      ),
      api.post_process(post_process.StepCommandContains, 'base_unittests', [
          'swarming',
          'collect',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'spec_error',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'base_unittests_run',
              'results_handler': 'bogus',
          }),
      test_spec_format_error(
          'contains a custom results_handler "bogus"'
          ' but that result handler was not found',
          step_name='isolated_scripts spec format error'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'merge_invalid',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'base_unittests_run',
              'merge': {
                  'script': 'path/to/script.py',
              },
              'swarming': {},
          }),
      test_spec_format_error(
          'contains a custom merge_script "path/to/script.py"'
          " that doesn't match the expected format"),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'precommit_args',
      try_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'base_unittests_run',
              'args': ['--should-be-in-output',],
              'precommit_args': ['--should-also-be-in-output',],
          }),
      api.post_process(post_process.StepCommandContains,
                       'base_unittests (with patch)', [
                           '--should-be-in-output',
                           '--should-also-be-in-output',
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'blink_web_tests_with_suffixes',
      ci_build(
          test_spec={
              'name': 'blink_web_tests',
              'test': 'webkit_tests',
              'results_handler': 'layout tests',
              'swarming': {
                  'dimensions': {
                      'os': 'Mac',
                      'gpu': '8086:blah',
                  },
              },
          }),
      api.post_process(post_process.StepSuccess,
                       'archive results for blink_web_tests'),
      api.post_process(post_process.DropExpectation),
  )

  def _generate_test_result(test_id, test_variant):
    return test_result_pb2.TestResult(
        test_id=test_id,
        variant=test_variant,
        expected=False,
        status=test_result_pb2.PASS,
    )

  correct_variant = resultdb_common.Variant()
  variant_def = getattr(correct_variant, 'def')
  variant_def['os'] = 'Mac-11'
  variant_def['test_suite'] = 'blink_web_tests'

  current_patchset_invocations = {
      'invocations/1':
          api.resultdb.Invocation(test_results=[
              _generate_test_result('ninja://:blink_web_tests/fast/test.html',
                                    correct_variant)
          ])
  }

  recent_run = test_history.QueryTestHistoryResponse(
      verdicts=[], next_page_token='dummy_token')

  yield api.test(
      'blink_web_tests_with_new_tests',
      try_build(
          test_spec={
              'name': 'blink_web_tests',
              'test': 'webkit_tests',
              'results_handler': 'layout tests',
              'swarming': {
                  'dimensions': {
                      'os': 'Mac-11',
                  },
              },
              'test_id_prefix': 'ninja://:blink_web_tests/'
          }),
      api.flakiness(check_for_flakiness=True,),
      # This overrides the file check to ensure that we have test files
      # in the given patch.
      api.step_data(
          'git diff to analyze patch (2)',
          api.raw_io.stream_output(
              'third_party/blink/web_tests/fast/test.html\n')),
      api.resultdb.query(
          inv_bundle=current_patchset_invocations,
          step_name=('collect tasks (with patch).'
                     'blink_web_tests results'),
      ),
      api.luci_analysis.query_test_history(
          recent_run,
          'ninja://:blink_web_tests/fast/test.html',
          parent_step_name='searching_for_new_tests',
      ),
      api.override_step_data(('test new tests for flakiness.'
                              'blink_web_tests '
                              '(check flakiness shard #0) on Mac-11'),
                             api.chromium_swarming.canned_summary_output(
                                 api.json.output({}), failure=False)),
      api.resultdb.query(
          inv_bundle=current_patchset_invocations,
          step_name=('test new tests for flakiness.'
                     'collect tasks (check flakiness shard #0).'
                     'blink_web_tests results')),
      api.post_process(
          post_process.StepSuccess, 'test new tests for flakiness.'
          'archive results for blink_web_tests (check flakiness shard #0)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'custom_webkit_tests_step_name',
      ci_build(
          test_spec={
              'name': 'custom_webkit_tests',
              'test': 'webkit_tests',
              'results_handler': 'layout tests',
              'swarming': {},
          }),
      api.post_process(post_process.MustRun, 'custom_webkit_tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'swarming_cipd_packages',
      ci_build(
          test_spec={
              'name': 'base_unittests',
              'test': 'base_unittests_run',
              'trigger_script': {
                  'script': '//path/to/script.py',
              },
              'swarming': {
                  'cipd_packages': [{
                      'cipd_package': 'cipd/package/name',
                      'location': '../../cipd/package/location',
                      'revision': 'version:1.0',
                  }],
              },
          }),
      api.post_process(
          post_process.StepCommandContains,
          'test_pre_run.[trigger (custom trigger script)] base_unittests', [
              '-cipd-package',
              '../../cipd/package/location:cipd/package/name=version:1.0',
          ]),
      api.post_process(post_process.DropExpectation),
  )
