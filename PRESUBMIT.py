# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Top-level presubmit script for the tools/build repo.

See http://dev.chromium.org/developers/how-tos/depottools/presubmit-scripts for
details on the presubmit API built into git cl.
"""

import re

PRESUBMIT_VERSION = '2.0.0'

USE_PYTHON3 = True


def CheckFreeze(input_api, output_api):
  return input_api.canned_checks.CheckInfraFreeze(input_api, output_api)


def GetFilesToSkip(input_api):
  return list(input_api.DEFAULT_FILES_TO_SKIP) + [
      # recipes.py and the .recipe_deps directory are created by the recipe
      # engine, so should not be checked as part of this repo
      r'^recipes/recipes\.py$',
      r'^recipes/\.recipe_deps/.*',
      # *_pb2.py files are generated from proto files and should not be linted
      r'.*_pb2.py$',
  ]


def join(input_api, *args):
  return input_api.os_path.normpath(
      input_api.os_path.join(input_api.PresubmitLocalPath(), *args)
  )


# The following files are executed using python3.8, so they should be analyzed
# using pylint 2.7 instead of the current pylint.
_PY3_8_FILES = (
    'hook-scripts/remove_orphaned_pycs.py',
    'recipes/bot_utils.py',
    'recipes/build_directory.py',
    'recipes/cloudtail_utils.py',
    'recipes/crash_utils.py',
    'recipes/daemonizer.py',
    'recipes/extract_build.py',
    'recipes/ninja_log_utils.py',
    'recipes/recipe_modules/adb/resources/list_devices.py',
    'recipes/recipe_modules/archive/resources/batch.py',
    'recipes/recipe_modules/archive/resources/filter_build_files.py',
    'recipes/recipe_modules/archive/resources/zip_archive.py',
    'recipes/recipe_modules/binary_size/resources/trybot_failed_expectations_checker.py',
    'recipes/recipe_modules/chromium_android/resources/archive_build_unittest.py',
    'recipes/recipe_modules/chromium_android/resources/archive_build.py',
    'recipes/recipe_modules/chromium_android/resources/authorize_adb_devices.py',
    'recipes/recipe_modules/chromium_android/resources/clean_local_files.py',
    'recipes/recipe_modules/chromium_swarming/resources/collect_task.py',
    'recipes/recipe_modules/chromium_swarming/resources/noop_merge.py',
    'recipes/recipe_modules/chromium_swarming/resources/wait_for_finished_task_set.py',
    'recipes/recipe_modules/chromium_swarming/unittests/collect_task_test.py',
    'recipes/recipe_modules/chromium_swarming/unittests/common_merge_script_tests.py',
    'recipes/recipe_modules/chromium_swarming/unittests/noop_merge_test.py',
    'recipes/recipe_modules/chromium_swarming/unittests/wait_for_finished_task_set_unittest.py',
    'recipes/recipe_modules/chromium_tests_builder_config/migration/scripts/buildozer_wrapper.py',
    'recipes/recipe_modules/chromium_tests_builder_config/migration/scripts/generate_groupings.py',
    'recipes/recipe_modules/chromium_tests_builder_config/migration/scripts/migrate.py',
    'recipes/recipe_modules/chromium_tests_builder_config/migration/scripts/tests/buildozer_wrapper_unit_test.py',
    'recipes/recipe_modules/chromium_tests_builder_config/migration/scripts/tests/generate_groupings_integration_test.py',
    'recipes/recipe_modules/chromium_tests_builder_config/migration/scripts/tests/generate_groupings_unit_test.py',
    'recipes/recipe_modules/chromium_tests_builder_config/migration/scripts/tests/migrate_integration_test.py',
    'recipes/recipe_modules/chromium_tests_builder_config/migration/scripts/tests/migrate_unit_test.py',
    'recipes/recipe_modules/chromium_tests/resources/archive_layout_test_results.py',
    'recipes/recipe_modules/chromium_tests/resources/find_command_lines.py',
    'recipes/recipe_modules/chromium/resources/export_tarball.py',
    'recipes/recipe_modules/chromium/resources/generate_hashes.py',
    'recipes/recipe_modules/chromium/resources/ninja_wrapper_test.py',
    'recipes/recipe_modules/chromium/resources/ninja_wrapper.py',
    'recipes/recipe_modules/code_coverage/resources/aggregation_util.py',
    'recipes/recipe_modules/code_coverage/resources/clean_up_java_coverage_files.py',
    'recipes/recipe_modules/code_coverage/resources/combine_jacoco_reports.py',
    'recipes/recipe_modules/code_coverage/resources/generate_coverage_metadata_for_java.py',
    'recipes/recipe_modules/code_coverage/resources/generate_coverage_metadata_for_javascript.py',
    'recipes/recipe_modules/code_coverage/resources/generate_coverage_metadata.py',
    'recipes/recipe_modules/code_coverage/resources/gerrit_util.py',
    'recipes/recipe_modules/code_coverage/resources/get_binaries_with_valid_coverage_data.py',
    'recipes/recipe_modules/code_coverage/resources/get_jacoco_and_jar_files_for_java.py',
    'recipes/recipe_modules/code_coverage/resources/get_native_libraries_for_android_target.py',
    'recipes/recipe_modules/code_coverage/resources/get_unstripped_paths.py',
    'recipes/recipe_modules/code_coverage/resources/make_report.py',
    'recipes/recipe_modules/code_coverage/resources/merge_metadata_files.py',
    'recipes/recipe_modules/code_coverage/resources/rebase_line_number_from_bot_to_gerrit.py',
    'recipes/recipe_modules/code_coverage/resources/repository_util.py',
    'recipes/recipe_modules/code_coverage/resources/write_paths_to_instrument.py',
    'recipes/recipe_modules/code_coverage/unittests/aggregation_util_test.py',
    'recipes/recipe_modules/code_coverage/unittests/blame_util_test.py',
    'recipes/recipe_modules/code_coverage/unittests/combine_jacoco_reports_test.py',
    'recipes/recipe_modules/code_coverage/unittests/constants_test.py',
    'recipes/recipe_modules/code_coverage/unittests/diff_util_test.py',
    'recipes/recipe_modules/code_coverage/unittests/generate_coverage_metadata_for_java_test.py',
    'recipes/recipe_modules/code_coverage/unittests/generate_coverage_metadata_for_javascript_test.py',
    'recipes/recipe_modules/code_coverage/unittests/generate_coverage_metadata_test.py',
    'recipes/recipe_modules/code_coverage/unittests/gerrit_util_test.py',
    'recipes/recipe_modules/code_coverage/unittests/get_native_libraries_for_android_target_test.py',
    'recipes/recipe_modules/code_coverage/unittests/get_unstripped_paths_test.py',
    'recipes/recipe_modules/code_coverage/unittests/make_report_test.py',
    'recipes/recipe_modules/code_coverage/unittests/merge_metadata_files_test.py',
    'recipes/recipe_modules/code_coverage/unittests/rebase_line_number_from_bot_to_gerrit_test.py',
    'recipes/recipe_modules/code_coverage/unittests/repository_util_test.py',
    'recipes/recipe_modules/code_coverage/unittests/write_paths_test.py',
    'recipes/recipe_modules/cronet/resources/generate_changelist.py',
    'recipes/recipe_modules/disk/resources/statvfs.py',
    'recipes/recipe_modules/flaky_reproducer/libs/strategies/parallel_strategy.py',
    'recipes/recipe_modules/flaky_reproducer/libs/test_binary/base_test_binary.py',
    'recipes/recipe_modules/flaky_reproducer/libs/test_binary/gtest_test_binary.py',
    'recipes/recipe_modules/flaky_reproducer/libs/test_binary/utils.py',
    'recipes/recipe_modules/flaky_reproducer/strategy_runner.py',
    'recipes/recipe_modules/flaky_reproducer/unittests/test_strategy_runner.py',
    'recipes/recipe_modules/flaky_reproducer/unittests/test_test_binary.py',
    'recipes/recipe_modules/goma/resources/cloudtail.py',
    'recipes/recipe_modules/perf_dashboard/resources/post_json.py',
    'recipes/recipe_modules/profiles/resources/load_merge_errors.py',
    'recipes/recipe_modules/reclient/resources/cloudtail_wrapper.py',
    'recipes/recipe_modules/reclient/resources/generate_rpl_gzip.py',
    'recipes/recipe_modules/reclient/resources/perform_health_check.py',
    'recipes/recipe_modules/symupload/resources/symupload_script.py',
    'recipes/recipe_modules/symupload/unittests/symupload_script_test.py',
    'recipes/recipe_modules/tar/resources/tar_test.py',
    'recipes/recipe_modules/tar/resources/tar.py',
    'recipes/recipe_modules/tar/resources/untar.py',
    'recipes/recipe_modules/test_utils/resources/archive_layout_test_results_summary.py',
    'recipes/recipe_modules/test_utils/resources/query_cq_flakes.py',
    'recipes/recipe_modules/test_utils/unittests/query_cq_flakes_test.py',
    'recipes/recipe_modules/tricium_clang_tidy/resources/tricium_clang_tidy_script.py',
    'recipes/recipe_modules/tricium_clang_tidy/resources/tricium_clang_tidy_test.py',
    'recipes/recipe_modules/v8_tests/resources/collect_v8_task_test.py',
    'recipes/recipe_modules/v8_tests/resources/collect_v8_task.py',
    'recipes/recipe_modules/v8/resources/build-dep-stats.py',
    'recipes/recipe_modules/webrtc/resources/binary_sizes.py',
    'recipes/recipes/android/sdk_packager.resources/parse_sdkmanager_list_test.py',
    'recipes/recipes/android/sdk_packager.resources/parse_sdkmanager_list.py',
    'recipes/recipes/chromium_rts/create_model.resources/integration_tests.py',
    'recipes/recipes/flakiness/generate_builder_test_data.resources/query_test.py',
    'recipes/recipes/flakiness/generate_builder_test_data.resources/query.py',
    'recipes/recipes/swarming/deterministic_build.resources/move.py',
    'recipes/runisolatedtest.py',
    'recipes/runtest.py',
    'recipes/tee.py',
    'recipes/unittests/bot_utils_test.py',
    'recipes/unittests/extract_build_test.py',
    'recipes/unittests/ninja_log_utils_test.py',
    'recipes/unittests/recipe_test.py',
    'recipes/unittests/runisolatedtest_test.py',
    'recipes/unittests/zip_build_test.py',
    'recipes/xvfb.py',
    'recipes/zip_build.py',
    'scripts/common/chromium_utils.py',
    'scripts/common/gtest_utils.py',
    'scripts/common/unittests/chromium_utils_test.py',
    'scripts/common/unittests/gtest_utils_test.py',
)


def CheckPylintOnCommit(input_api, output_api):
  disabled_warnings = [
      'C0321',  # More than one statement on a single line
      'W0613',  # Unused argument
      'C3001',  # unnecessary-lambda-assignment

      # f-strings should generally be preferred since they are faster than other
      # forms of string construction and often (but not always) easier to read.
      # The performance difference is unlikely to matter unless many strings are
      # being constructed in a tight loop, so addressing the many findings of
      # this category is unlikely to provide much performance benefit and so
      # shouldn't be a priority. It would be nice if we could enable this only
      # for new lines...
      'C0209',  # consider-using-f-string

      # dict literals are more performant than using a dict call, but a little
      # more noisy visually and the performance difference is unlikely to matter
      # unless the dict is being constructed in a tight loop
      'R1735',  # use-dict-literal

      # Below warnings are disabled for pylint version update, but better to
      # remove if possible.

      # There are a number of long lines in the repo already, some of them in
      # code that the formatter does not play nicely with. There is also a
      # warning presubmit check that flags lines that are too long only in
      # modified files, so it's not vital to have pylint cause errors for this.
      'C0301',  # line-too-long

      # This will require adding many additional return statements and in turn
      # will require test cases to be added/modified to get coverage on the new
      # return statements
      'R1710',  # inconsistent-return-statements
  ]
  extra_paths_list = [
      join(input_api, 'recipes'),
      join(input_api, 'scripts'),
      # Initially, a separate run was done for unit tests but now that
      # pylint is fetched in memory with setuptools, it seems it caches
      # sys.path so modifications to sys.path aren't kept.
      join(input_api, 'recipes', 'unittests'),
  ]
  py3_8_files = [f'{re.escape(x)}$' for x in _PY3_8_FILES]
  lints = input_api.canned_checks.RunPylint(
      input_api,
      output_api,
      files_to_skip=GetFilesToSkip(input_api) + py3_8_files,
      disabled_warnings=disabled_warnings,
      extra_paths_list=extra_paths_list,
      version='2.17',
  )
  lints.extend(
      input_api.canned_checks.RunPylint(
          input_api,
          output_api,
          files_to_check=py3_8_files,
          disabled_warnings=disabled_warnings,
          extra_paths_list=extra_paths_list,
          version='2.7',
      )
  )
  return lints


def _GetTests(input_api, output_api, test_files):
  return input_api.canned_checks.GetUnitTests(
      input_api,
      output_api,
      sorted(test_files),
      run_on_python3=True,
      run_on_python2=False,
      skip_shebang_check=True,
  )


# The following tests invoke recipes.py which isn't safe in parallel, so they'll
# be run in a separate check function that runs them sequentially
RECIPES_PY_TESTS = [
    'recipes/unittests/recipe_test.py',
    (
        'recipes/recipe_modules/chromium_tests_builder_config/migration/'
        'scripts/tests/generate_groupings_integration_test.py'
    ),
    (
        'recipes/recipe_modules/chromium_tests_builder_config/migration/'
        'scripts/tests/migrate_integration_test.py'
    ),
]


def _RecipesPyTestFiles(input_api):
  return [join(input_api, f) for f in RECIPES_PY_TESTS]


def CheckRecipesPyTestsOnCommit(input_api, output_api):
  tests = _GetTests(input_api, output_api, _RecipesPyTestFiles(input_api))
  return input_api.RunTests(tests, parallel=False)


def CheckTestsOnCommit(input_api, output_api):
  excluded_test_files = set(_RecipesPyTestFiles(input_api))

  # query_unittest.py is not meant to be automated because it operates on
  # live tables.
  excluded_test_files.update(
      'recipes/recipes/chromium/autosharder.resources/query_unittest.py'
  )

  test_files = []
  for dir_glob in (
      ('recipes', 'unittests'),
      ('recipes', 'recipe_modules', '*', 'unittests'),
      ('recipes', 'recipe_modules', '*', 'resources'),
      ('recipes', 'recipe_modules', 'chromium_tests_builder_config',
       'migration', 'scripts', 'tests'),
      ('recipes', 'recipes', '*.resources'),
      ('recipes', 'recipes', '*', '*.resources'),
      ('scripts', 'common', 'unittests'),
  ):
    glob = dir_glob + ('*_test.py',)
    test_files.extend(
        x for x in input_api.glob(join(input_api, *glob))
        if x not in excluded_test_files
    )

  tests = _GetTests(input_api, output_api, test_files)
  return input_api.RunTests(tests)


def CheckPanProjectChecks(input_api, output_api):
  return input_api.canned_checks.PanProjectChecks(
      input_api,
      output_api,
      excluded_paths=GetFilesToSkip(input_api),
      owners_check=False
  )


def CheckConfigFilesParse(input_api, output_api):
  file_filter = lambda x: x.LocalPath() == 'infra/config/recipes.cfg'
  return input_api.canned_checks.CheckJsonParses(
      input_api, output_api, file_filter=file_filter
  )


def CheckPatchFormatted(input_api, output_api):
  # TODO(https://crbug.com/979330) If clang-format is fixed for non-chromium
  # repos, remove check_clang_format=False so that proto files can be formatted
  return input_api.canned_checks.CheckPatchFormatted(
      input_api,
      output_api,
      check_clang_format=False,
      result_factory=output_api.PresubmitError,
  )
