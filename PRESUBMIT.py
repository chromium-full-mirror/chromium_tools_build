# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Top-level presubmit script for the tools/build repo.

See http://dev.chromium.org/developers/how-tos/depottools/presubmit-scripts for
details on the presubmit API built into git cl.
"""

PRESUBMIT_VERSION = '2.0.0'

USE_PYTHON3 = True

_IGNORE_FREEZE_FOOTER = 'Ignore-Freeze'

# The time module's handling of timezones is abysmal, so the boundaries are
# precomputed in UNIX time
_FREEZE_START = 1671177600  # 2022/12/16 00:00 -0800
_FREEZE_END = 1672646400  # 2023/01/02 00:00 -0800


def CheckFreeze(input_api, output_api):
  if _FREEZE_START <= input_api.time.time() < _FREEZE_END:
    footers = input_api.change.GitFootersFromDescription()
    if _IGNORE_FREEZE_FOOTER not in footers:

      def convert(t):
        ts = input_api.time.localtime(t)
        return input_api.time.strftime('%Y/%m/%d %H:%M %z', ts)

      return [
          output_api.PresubmitError(
              'There is a prod freeze in effect from {} until {}'.format(
                  convert(_FREEZE_START), convert(_FREEZE_END)
              )
          )
      ]

  return []

# A list of file that are executed using python2. Tests for these are
# run using vpython2 and lints are performed with pylint 1.5. We should
# work to eliminate this list by migrating scripts to python3 (updating
# it so that they are invoked using python3 or vpython3).
PYTHON2_FILES = (
    'recipes/bot_utils.py',
    'recipes/recipe_modules/adb/resources/list_devices.py',
    (
        'recipes/recipe_modules/cronet/resources/'
        'upload_perf_dashboard_results_test.py'
    ),
    'recipes/recipe_modules/cronet/resources/upload_perf_dashboard_results.py',
    'recipes/recipe_modules/disk/resources/statvfs.py',
    'recipes/recipe_modules/findit/resources/check_target_existence.py',
    'recipes/recipe_modules/symupload/resources/symupload.py',
    'recipes/recipe_modules/symupload/unittests/symupload_test.py',
    'recipes/results_dashboard.py',
    'recipes/runisolatedtest.py',
    'recipes/unittests/bot_utils_test.py',
    'recipes/unittests/recipe_test.py',
    'recipes/unittests/results_dashboard_test.py',
    'recipes/unittests/runisolatedtest_test.py',
    'scripts/common/__init__.py',
    'scripts/common/chromium_utils.py',
)


def GetFilesToSkip(input_api):
  return list(input_api.DEFAULT_FILES_TO_SKIP) + [
      r'.*recipes/.*/build.*/.',
      r'.*recipes/.*/isolate.*/.',
      r'.*depot_tools/.*',
      r'.*goma/.*',
      r'.*scripts/release/.*',
      r'.*recipes/recipes.py$',
      r'.*recipes/recipes/.*_autogen.py$',

      # Exclude all "...recipe_deps" directories.
      #
      # These directories are created by recipe engine.
      # Each is an independent recipe checkout. If Pylint is run on
      # these, it will hang forever, so we must exclude them.
      r'^(.*/)?\..*recipe_deps/.*',
  ]


def join(input_api, *args):
  return input_api.os_path.normpath(
      input_api.os_path.join(input_api.PresubmitLocalPath(), *args)
  )


def CheckPylintOnCommit(input_api, output_api):
  disabled_warnings = [
      'C0321',  # More than one statement on a single line
      'W0613',  # Unused argument
      'W0403',  # Relative import. TODO(crbug.com/1095510): remove this

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
  lints = input_api.canned_checks.RunPylint(
      input_api,
      output_api,
      files_to_skip=GetFilesToSkip(input_api) + list(PYTHON2_FILES),
      disabled_warnings=disabled_warnings,
      extra_paths_list=extra_paths_list,
  )
  if PYTHON2_FILES:
    lints.extend(
        input_api.canned_checks.RunPylint(
            input_api,
            output_api,
            files_to_check=PYTHON2_FILES,
            disabled_warnings=disabled_warnings,
            extra_paths_list=extra_paths_list,
            version='1.5',
        )
    )
  return lints


def _GetTests(input_api, output_api, test_files):
  python2_files = set(join(input_api, f) for f in PYTHON2_FILES)
  non_python2_test_files = []
  python2_test_files = []
  for t in sorted(test_files):
    if t in python2_files:
      python2_test_files.append(t)
    else:
      non_python2_test_files.append(t)

  tests = []
  tests.extend(
      input_api.canned_checks.GetUnitTests(
          input_api,
          output_api,
          non_python2_test_files,
          run_on_python3=True,
          run_on_python2=False,
          skip_shebang_check=True,
      )
  )
  tests.extend(
      input_api.canned_checks.GetUnitTests(
          input_api,
          output_api,
          python2_test_files,
          run_on_python2=True,
          run_on_python3=False,
          skip_shebang_check=True,
      )
  )
  return tests


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


def CheckPanProjectChecksOnCommit(input_api, output_api):
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
      input_api, output_api, check_clang_format=False
  )
