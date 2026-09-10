# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Indexes Chromium source code into a CodeQL database and stores the result.

This requires compiling the Chromium `all` target before building the index.

Upload destination is specified by UPLOAD_BUCKET.
"""

from recipe_engine import post_process
from recipe_engine.recipe_api import Property
from PB.recipes.build.chrome_codeql import InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import (
  bot_update,
  depot_tools,
  gclient,
  gsutil,
)
from RECIPE_MODULES.infra import zip as zip_module
from RECIPE_MODULES.recipe_engine import (
  cipd,
  context,
  file,
  path,
  properties,
  raw_io,
  step,
  time,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  cipd: cipd.API
  context: context.API
  depot_tools: depot_tools.API
  file: file.API
  gclient: gclient.API
  gsutil: gsutil.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  time: time.API
  zip: zip_module.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API


PROPERTIES = InputProperties

UPLOAD_BUCKET = 'chrome-codeql-databases'


def RunSteps(api: DEPS, properties):
  if not properties.codeql_version:
    raise api.step.StepFailure('No CodeQL version provided')
  api.gclient.set_config('chromium')
  api.chromium.set_config()
  update_result = api.bot_update.ensure_checkout()
  source_dir = update_result.source_root.path
  api.gclient.runhooks()
  build_dir = source_dir / 'out/release'
  gn_path = api.depot_tools.gn_py_path
  ninja_path = source_dir / 'third_party/ninja/ninja'
  cipd_root = api.path.start_dir / 'cipd'
  raw_databases_path = api.path.mkdtemp('codeql_dbs')
  with api.context(cwd=source_dir, env_suffixes={'PATH': [cipd_root]}):
    codeql_root = api.path.start_dir / 'codeql'
    ensure_file = api.cipd.EnsureFile().add_package(
      'infra/3pp/tools/codeql/${platform}', properties.codeql_version
    )
    api.cipd.ensure(codeql_root, ensure_file)
    codeql_path = codeql_root / 'codeql'
    api.step(
      'gn gen out/release',
      ['python3', gn_path, 'gen', build_dir, '--args=use_remoteexec=false'],
    )
    codeql_script_path = source_dir.joinpath(
      'tools', 'codeql', 'index_target.py'
    )
    api.step(
      'index_target.py',
      [
        'vpython3',
        codeql_script_path,
        '--out_path',
        build_dir,
        '--db_path',
        raw_databases_path,
        '--codeql_binary_path',
        codeql_path,
        '--gn_path',
        gn_path,
        '--ninja_path',
        ninja_path,
      ],
    )

    # TODO(flowerhack): In a future CL (after we're uploading logs and
    # databases separately), provide an error report for all DBs, not just
    # Chrome.
    chrome_log_path = raw_databases_path / 'chrome/log'
    validate_database_script_path = source_dir.joinpath(
      'tools', 'codeql', 'validate_database.py'
    )
    # ok_ret='any', because we want to upload the results even if there's
    # errors.
    api.step(
      'validate_database.py',
      ['vpython3', validate_database_script_path, '-l', chrome_log_path],
      ok_ret='any',
    )

    codeql_dbs_out_dir = api.path.start_dir / 'codeql_dbs'
    api.file.ensure_directory("ensure codeql_dbs_out_dir", codeql_dbs_out_dir)
    codeql_dbs_with_logs_out_dir = api.path.start_dir / 'codeql_dbs_with_logs'
    api.file.ensure_directory(
      "ensure codeql_dbs_with_logs_out_dir", codeql_dbs_with_logs_out_dir
    )

    raw_logs_path = api.path.start_dir / 'raw_codeql_logs'
    api.file.ensure_directory("ensure raw_logs_path", raw_logs_path)

    cur_date_str = api.time.utcnow().strftime('%Y-%m-%d-%H:%M:%S')
    TEST_DATA = ['chrome']
    list_of_raw_database_paths = api.file.listdir(
      'get list of codeql db paths', raw_databases_path, test_data=TEST_DATA
    )
    cloud_folder_name = 'codeql-' + cur_date_str
    # Local directory structure looks like:
    # db_path/ (raw_databases_path)
    #   ./chrome
    #   ./chrome/db-cpp
    #   ./chrome/log...
    #
    # codeql_dbs/ (codeql_dbs_out_dir)
    # codeql_dbs_with_logs/ (codeql_dbs_with_logs_out_dir)
    #
    # We're going to make it into
    # chrome_dbs/chrome/['bundled' zip produced by `codeql database bundle`]
    # chrome_dbs_with_logs/chrome/[ZIPPED CONTENTS OF /db_path/chrome]
    #
    # Then in the end we'll upload chrome_dbs and chrome_dbs_with_logs.
    for raw_database_path in list_of_raw_database_paths:
      database_basename = api.path.basename(raw_database_path)  # e.g. 'chrome'

      # Bundle up the contents of the raw_database_path.
      # e.g. bundle up db_path/libavif and put the resulting zipfile in codeql_dbs_out_dir
      database_zip_out_filename = (
        database_basename + '-codeql-' + cur_date_str + '-database.zip'
      )
      database_zip_out_path = codeql_dbs_out_dir / database_zip_out_filename
      api.step(
        'codeql database bundle',
        [
          codeql_path,
          'database',
          'bundle',
          '-o',
          database_zip_out_path,
          '--',
          raw_database_path,
        ],
      )

      # Zip up the contents of raw_database_path.
      # e.g. zip up db_path/libavif and put the resulting zipfile in
      # codeql_dbs_with_logs_out_dir
      logs_zip_out_filename = (
        database_basename
        + '-codeql-'
        + cur_date_str
        + '-database-with-logs.zip'
      )
      logs_zip_out_path = codeql_dbs_with_logs_out_dir / logs_zip_out_filename
      api.zip.directory('zip codeql dir', raw_database_path, logs_zip_out_path)

    api.gsutil.upload(
      codeql_dbs_out_dir,
      UPLOAD_BUCKET,
      cloud_folder_name,
      args=['-r'],
      link_name='CodeQL databases',
    )
    api.gsutil.upload(
      codeql_dbs_with_logs_out_dir,
      UPLOAD_BUCKET,
      cloud_folder_name,
      args=['-r'],
      link_name='CodeQL databases with logs',
    )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.properties(InputProperties(codeql_version='latest')),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'no CodeQL version provided',
    api.properties(InputProperties(codeql_version='')),
    api.post_process(post_process.DropExpectation),
    status='FAILURE',
  )
