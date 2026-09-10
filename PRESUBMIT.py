# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Top-level presubmit script for the tools/build repo.

See http://dev.chromium.org/developers/how-tos/depottools/presubmit-scripts for
details on the presubmit API built into git cl.
"""

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


def CheckPylintOnCommit(input_api, output_api):
  extra_paths_list = [
    join(input_api, 'recipes'),
    join(input_api, 'scripts'),
    # Initially, a separate run was done for unit tests but now that
    # pylint is fetched in memory with setuptools, it seems it caches
    # sys.path so modifications to sys.path aren't kept.
    join(input_api, 'recipes', 'unittests'),
  ]
  # 'cyclic-import' must be explicitly listed in disabled_warnings here to prevent
  # depot_tools from spawning a separate single-threaded '--enable=cyclic-import'
  # process across all files. Cyclic imports exist naturally in recipe module
  # architecture between __init__.py and api.py/test_api.py (e.g. in
  # recipe_modules/chromium_tests_builder_config and recipe_modules/tricium_clang_tidy).
  return input_api.canned_checks.RunPylint(
    input_api,
    output_api,
    files_to_skip=GetFilesToSkip(input_api),
    disabled_warnings=['cyclic-import'],
    pylintrc='pylintrc-3.2',
    extra_paths_list=extra_paths_list,
    version='3.2',
  )


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
    (
      'recipes',
      'recipe_modules',
      'chromium_tests_builder_config',
      'migration',
      'scripts',
      'tests',
    ),
    ('recipes', 'recipes', '*.resources'),
    ('recipes', 'recipes', '*', '*.resources'),
    ('scripts', 'common', 'unittests'),
  ):
    glob = dir_glob + ('*_test.py',)
    test_files.extend(
      x
      for x in input_api.glob(join(input_api, *glob))
      if x not in excluded_test_files
    )

  tests = _GetTests(input_api, output_api, test_files)
  return input_api.RunTests(tests)


def CheckPanProjectChecks(input_api, output_api):
  return input_api.canned_checks.PanProjectChecks(
    input_api,
    output_api,
    excluded_paths=GetFilesToSkip(input_api),
    owners_check=False,
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
