# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import copy

from recipe_engine import post_process

from RECIPE_MODULES.build.binary_size import constants

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import binary_size, filter as filter_module
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  file,
  json,
  platform,
  properties,
  time,
)


@dataclass
class DEPS(RecipeScriptApi):
  binary_size: binary_size.API
  file: file.API
  filter: filter_module.API
  json: json.API
  platform: platform.API
  properties: properties.API
  time: time.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  binary_size: binary_size.TEST_API
  file: file.TEST_API
  filter: filter_module.TEST_API
  json: json.TEST_API
  time: time.TEST_API
  tryserver: tryserver.TEST_API


def RunSteps(api: DEPS):
  return api.binary_size.android_binary_size(
    chromium_config='chromium',
    chromium_apply_configs=['mb'],
    gclient_config='chromium',
    gclient_apply_configs=['android'],
    try_gs_analysis=True,
  )


def GenTests(api: TEST_DEPS):

  def override_expectation_to_fail(with_patch=True, use_alternative=False):
    suffix = ' (with patch)' if with_patch else ' (without patch)'
    failed_messages = ['Failure Message A']
    if use_alternative:
      failed_messages += ['Failure Message B']
    return api.step_data(
      'Run Expectations Script' + suffix,
      api.json.output(
        {
          'success': False,
          'failed_messages': failed_messages,
        }
      ),
    )

  def has_binary_size_property(check, steps):
    check(steps['analyze'].output_properties['binary_size_plugin'] is not None)

  yield api.test(
    'noop_because_of_analyze',
    api.binary_size.build(override_commit_log=True),
    api.filter.no_dependency(),
    api.post_check(has_binary_size_property),
    api.post_process(post_process.MustRun, 'analyze'),
    api.post_process(post_process.DoesNotRunRE, r'.*compile'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'runs_on_readme_chromium',
    api.binary_size.build(override_commit_log=True),
    api.tryserver.get_files_affected_by_patch(
      ['src/third_party/foo/README.chromium']
    ),
    api.post_process(post_process.MustRun, 'compile (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'compile_failure',
    api.binary_size.build(override_commit_log=True),
    api.override_step_data('compile (with patch)', retcode=1),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'patch_fixes_build',
    api.binary_size.build(),
    api.override_step_data('compile (without patch)', retcode=1),
    api.time.seed(constants.TEST_TIME + 7230),
    api.post_process(
      post_process.MustRun, constants.PATCH_FIXED_BUILD_STEP_NAME
    ),
    api.post_process(post_process.DropExpectation),
  )

  def has_expected_supersize_link(
    check, steps, bucket=constants.RESULTS_GS_BUCKET
  ):
    expected_url = 'https://foo.com/{}'.format(
      constants.ARCHIVED_URL_FMT.format(
        bucket=bucket,
        dest='{}/{}/{}/result.ndjson'.format(
          constants.TEST_BUILDER,
          constants.TEST_TIME_FMT,
          constants.TEST_BUILDNUMBER,
        ),
      )
    )
    check(
      steps[constants.RESULTS_STEP_NAME].links['Supersize HTML Diff']
      == expected_url
    )

  def has_expected_binary_size_url(
    check, steps, bucket=constants.RESULTS_GS_BUCKET
  ):
    expected_url = constants.ARCHIVED_URL_FMT.format(
      bucket=bucket,
      dest='{}/{}/{}/result.txt'.format(
        constants.TEST_BUILDER,
        constants.TEST_TIME_FMT,
        constants.TEST_BUILDNUMBER,
      ),
    )
    results_step = steps[constants.RESULTS_STEP_NAME]
    binary_size_prop = results_step.output_properties['binary_size_plugin']
    actual_url = binary_size_prop['extras'][-1]['url']
    check(actual_url == expected_url)

  def final_step_is_not_nested(check, steps):
    # This test is to make sure that binary_size._synthesize_log_link logs
    # linking logic is correct. If this fails, please make sure to fix the
    # format string in _synthesize_log_link alongside this test.

    # checks that step name constant is not already nested
    check('.' not in constants.RESULTS_STEP_NAME)
    # checks that step name actually exists (i.e. not nested in another step).
    check(constants.RESULTS_STEP_NAME in steps)
    # checks that first character of step name is not illegal.
    check(constants.RESULTS_STEP_NAME[0].isalnum())
    # checks that this is actually the final/relevant step.
    check(
      'binary_size_plugin'
      in (steps[constants.RESULTS_STEP_NAME].output_properties)
    )

  yield api.test(
    'normal_build',
    api.binary_size.build('normal_build', override_commit_log=True),
    api.post_check(has_expected_supersize_link),
    api.post_check(has_expected_binary_size_url),
    api.post_check(final_step_is_not_nested),
    # Make sure some steps that only get triggered on a package restructure
    # are not run normally.
    api.post_process(post_process.DoesNotRun, 'bot_update (without patch)'),
    api.post_process(
      post_process.DoesNotRun, 'gclient runhooks (with patch again)'
    ),
    # Make sure some steps that do not get triggered on a package restructure
    # are run normally.
    api.post_process(
      post_process.StepSuccess, 'Commit log for uploaded revision'
    ),
    api.post_process(post_process.StepSuccess, 'gsutil Downloading zip'),
    api.post_process(post_process.StepSuccess, constants.RESULTS_STEP_NAME),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'normal_nondefault_targets',
    api.binary_size.build('nondefault_targets', override_commit_log=True),
    api.binary_size.properties(
      analyze_targets=['//foo:bar_binary'], compile_targets=['bar_binary']
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'normal_significant_binary_package_restructure',
    api.binary_size.build('normal build + significant package restructure'),
    api.binary_size.on_significant_binary_package_restructure(),
    api.post_check(has_expected_supersize_link),
    api.post_check(has_expected_binary_size_url),
    api.post_check(final_step_is_not_nested),
    # Make sure some steps that normally get triggered are not run here.
    api.post_process(
      post_process.DoesNotRun, 'Commit log for uploaded revision'
    ),
    api.post_process(post_process.DoesNotRun, 'gsutil Downloading zip'),
    # Make sure some steps that are not normally triggered are run here.
    api.post_process(post_process.StepSuccess, 'bot_update (without patch)'),
    api.post_process(
      post_process.StepSuccess, 'gclient runhooks (with patch again)'
    ),
    api.post_process(post_process.StepSuccess, constants.RESULTS_STEP_NAME),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'unexpected_increase',
    api.binary_size.build(override_commit_log=True),
    api.override_step_data(
      constants.RESULT_JSON_STEP_NAME,
      api.file.read_json(
        {
          'status_code': 1,
          'summary': '\n!summary!',
          'archive_filenames': [],
          'links': [],
        }
      ),
    ),
    api.post_process(post_process.StepFailure, constants.RESULTS_STEP_NAME),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  def has_failed_expectations(check, steps):
    check(
      steps[constants.EXPECTATIONS_STEP_NAME].logs['failed expectations']
      is not None
    )

  yield api.test(
    'expectations_file_warning',
    api.binary_size.build(override_commit_log=True),
    api.override_step_data('bot_update', retcode=1),
    override_expectation_to_fail(with_patch=True),
    override_expectation_to_fail(with_patch=False),
    api.post_process(
      post_process.StepWarning, constants.EXPECTATIONS_STEP_NAME
    ),
    api.post_check(has_failed_expectations),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'expectations_file_failure',
    api.binary_size.build(override_commit_log=True),
    override_expectation_to_fail(with_patch=True),
    api.post_process(
      post_process.StepFailure, constants.EXPECTATIONS_STEP_NAME
    ),
    api.post_check(has_failed_expectations),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'expectations_file_failure_with_note',
    api.binary_size.build(override_commit_log=True),
    api.override_step_data('bot_update', retcode=1),
    override_expectation_to_fail(with_patch=True),
    override_expectation_to_fail(with_patch=False, use_alternative=True),
    api.post_process(
      post_process.StepFailure, constants.EXPECTATIONS_STEP_NAME
    ),
    api.post_check(has_failed_expectations),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'expectations_file_failure_skipped_due_to_footer',
    api.binary_size.build(
      override_commit_log=True,
      extra_footers={
        constants.SKIP_EXPECTATIONS_FOOTER_KEY: ["Reasons to skip"]
      },
    ),
    api.override_step_data('bot_update', retcode=1),
    override_expectation_to_fail(with_patch=True),
    override_expectation_to_fail(with_patch=False, use_alternative=True),
    api.post_process(
      post_process.StepFailure, constants.EXPECTATIONS_STEP_NAME
    ),
    api.post_check(has_failed_expectations),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'clear_expectation_files_ignores_failure',
    api.binary_size.build(override_commit_log=True),
    api.override_step_data('Clear Expectation Files', retcode=1),
    api.post_process(post_process.StepSuccess, 'Clear Expectation Files'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'pass_because_of_size_footer',
    api.binary_size.build(android_size_footer=True, override_commit_log=True),
    api.override_step_data(
      constants.RESULT_JSON_STEP_NAME,
      api.file.read_json(
        {
          'status_code': 1,
          'summary': '\n!summary!',
          'archive_filenames': [],
          'links': [],
        }
      ),
    ),
    api.post_process(post_process.StepSuccess, constants.RESULTS_STEP_NAME),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'fail_because_of_wrong_size_footer',
    api.binary_size.build(fuchsia_size_footer=True, override_commit_log=True),
    api.override_step_data(
      constants.RESULT_JSON_STEP_NAME,
      api.file.read_json(
        {
          'status_code': 1,
          'summary': '\n!summary!',
          'archive_filenames': [],
          'links': [],
        }
      ),
    ),
    api.post_process(post_process.StepFailure, constants.RESULTS_STEP_NAME),
    api.post_process(post_process.DropExpectation),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'pass_because_of_revert',
    api.binary_size.build(
      commit_message='Revert some change', override_commit_log=True
    ),
    api.override_step_data(
      constants.RESULT_JSON_STEP_NAME,
      api.file.read_json(
        {
          'status_code': 1,
          'summary': '\n!summary!',
          'archive_filenames': [],
          'links': [],
        }
      ),
    ),
    api.post_process(post_process.StepSuccess, constants.RESULTS_STEP_NAME),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'nondefault_results_bucket',
    api.binary_size.build(override_commit_log=True),
    api.binary_size.properties(results_bucket='fake-results-bucket'),
    api.post_check(has_expected_supersize_link, bucket='fake-results-bucket'),
    api.post_check(has_expected_binary_size_url, bucket='fake-results-bucket'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'valid_latest_file',
    api.binary_size.build(override_commit_log=True),
    api.post_process(post_process.MustRun, 'gsutil Downloading zip'),
    api.post_process(post_process.DoesNotRun, 'compile (without patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'valid_latest_file_merge_conflict',
    api.binary_size.build(override_commit_log=True),
    api.override_step_data('bot_update', retcode=1),
    api.post_process(post_process.MustRun, 'bot_update (2)'),
    api.post_process(post_process.DoesNotRun, 'gsutil Downloading zip'),
    api.post_process(post_process.MustRun, 'compile (without patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'invalid_latest_file',
    api.binary_size.build(),
    api.time.seed(constants.TEST_TIME + 7230),
    api.post_process(post_process.DoesNotRun, 'gsutil Downloading zip'),
    api.post_process(post_process.MustRun, 'compile (without patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'patch_parent_rev_too_new',
    api.binary_size.build(
      recent_upload_cp=12345, patch_parent_cp=12350, override_commit_log=True
    ),
    api.post_process(post_process.DoesNotRun, 'gsutil Downloading zip'),
    api.post_process(post_process.MustRun, 'compile (without patch)'),
    api.post_check(has_expected_supersize_link),
    api.post_check(has_expected_binary_size_url),
    api.post_check(final_step_is_not_nested),
    api.post_process(post_process.StepSuccess, constants.RESULTS_STEP_NAME),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'patch_parent_no_cp',
    api.binary_size.build(override_commit_log=True),
    api.binary_size.override_cp_for_parents(count=1),
    api.post_process(post_process.MustRun, 'gsutil Downloading zip'),
    api.post_process(post_process.DoesNotRun, 'compile (without patch)'),
    api.post_check(has_expected_supersize_link),
    api.post_check(has_expected_binary_size_url),
    api.post_check(final_step_is_not_nested),
    api.post_process(post_process.StepSuccess, constants.RESULTS_STEP_NAME),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'patch_parent_no_cp_max_limit',
    api.binary_size.build(override_commit_log=True),
    api.binary_size.override_cp_for_parents(count=10, add_final_parent=False),
    api.post_process(post_process.DoesNotRun, 'gsutil Downloading zip'),
    api.post_process(post_process.MustRun, 'compile (without patch)'),
    api.post_check(has_expected_supersize_link),
    api.post_check(has_expected_binary_size_url),
    api.post_check(final_step_is_not_nested),
    api.post_process(post_process.StepSuccess, constants.RESULTS_STEP_NAME),
    api.post_process(post_process.DropExpectation),
  )

  results_json = copy.deepcopy(constants.TEST_RESULT_JSON)
  results_json['gerrit_plugin_details']['listings'].append(
    {
      'name': 'arm64 size',
      'delta': '500 bytes',
      'allowed': False,
      'large_improvement': False,
      'log_name': 'resource_sizes_64_log',
    }
  )
  yield api.test(
    'arm64_supersize_normal',
    api.binary_size.build(override_commit_log=True),
    api.binary_size.properties(arm64_size_config_json='fake-arm64-config.json'),
    api.override_step_data(
      constants.RESULT_JSON_STEP_NAME, api.file.read_json(results_json)
    ),
    api.post_process(post_process.DoesNotRun, 'compile (with patch arm64)'),
    api.post_process(
      post_process.MustRun, constants.RESULT_JSON_STEP_NAME + ' (2)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  # Test CreateArm64SizeReport footer as well as not using gs bucket results.
  results_json['gerrit_plugin_details']['listings'][-1]['allowed'] = True
  yield api.test(
    'arm64_supersize_footer_and_no_gsutil',
    api.binary_size.build(
      recent_upload_cp=12345,
      patch_parent_cp=12350,
      extra_footers={'CreateArm64SizeReport': ['true']},
      override_commit_log=True,
    ),
    api.binary_size.properties(arm64_size_config_json='fake-arm64-config.json'),
    api.override_step_data(
      constants.RESULT_JSON_STEP_NAME, api.file.read_json(results_json)
    ),
    api.post_process(post_process.DoesNotRun, 'gsutil Downloading zip'),
    api.post_process(post_process.MustRun, 'compile (with patch arm64)'),
    api.post_process(
      post_process.MustRun, constants.RESULT_JSON_STEP_NAME + ' (2)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'arm64_supersize_compile_fail',
    api.binary_size.build(
      recent_upload_cp=12345,
      patch_parent_cp=12350,
      extra_footers={'CreateArm64SizeReport': ['true']},
      override_commit_log=True,
    ),
    api.binary_size.properties(arm64_size_config_json='fake-arm64-config.json'),
    api.override_step_data(
      constants.RESULT_JSON_STEP_NAME, api.file.read_json(results_json)
    ),
    api.override_step_data('compile (with patch arm64)', retcode=1),
    api.post_process(
      post_process.DoesNotRun, constants.RESULT_JSON_STEP_NAME + ' (2)'
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
