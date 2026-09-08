# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Takes the most recent Chromium CodeQL database, runs the queries in
//src/tools/codeql/queries against that database, and stores the results in
a cloud bucket.

Upload destination is specified by UPLOAD_BUCKET.
"""

from recipe_engine import post_process
from recipe_engine.recipe_api import Property
from PB.recipes.build.chrome_codeql import InputProperties
import re

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_tests, chromium_tests_builder_config
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
  bot_update: bot_update.TEST_API
  chromium: chromium.TEST_API
  file: file.TEST_API
  gclient: gclient.TEST_API
  path: path.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API

DOWNLOAD_BUCKET = 'chrome-codeql-databases'
UPLOAD_BUCKET = 'chrome-codeql-query-results'
DOWNLOAD_URL_PREFIX = 'https://storage.googleapis.com/chrome-codeql-databases/'
DATABASE_FILE_SUFFIX = "database.zip"
PROPERTIES = InputProperties


def download_database(api: DEPS, db_name, basename_of_directory, datetime):
  path_to_db_within_bucket = (
      f'{basename_of_directory}/'
      f'{db_name}-codeql-{datetime}-{DATABASE_FILE_SUFFIX}')
  downloaded_db_basename = f'{db_name}-codeql-{datetime}-{DATABASE_FILE_SUFFIX}'
  # Download the relevant DB
  downloaded_databases_dir = api.path.start_dir / 'downloaded_databases'
  api.file.ensure_directory("ensure downloaded_databases_dir",
                            downloaded_databases_dir)
  api.gsutil.download(DOWNLOAD_BUCKET, path_to_db_within_bucket,
                      downloaded_databases_dir)
  local_path_to_db = f'{downloaded_databases_dir}/{downloaded_db_basename}'
  unzipped_databases_dir = api.path.start_dir / 'unzipped_databases'
  # this api requires that unzipped_databases_dir not actually exist lol
  api.zip.unzip("unzip database", local_path_to_db, unzipped_databases_dir)
  unzipped_db_path = unzipped_databases_dir / db_name
  return unzipped_db_path


def checkout_chromium(api: DEPS):
  # Fetches Chromium source and returns the location.
  api.gclient.set_config('chromium')
  api.chromium.set_config()
  update_result = api.bot_update.ensure_checkout()
  source_dir = update_result.source_root.path
  api.gclient.runhooks()
  return source_dir


def RunSteps(api: DEPS, properties):
  if not properties.codeql_version:
    raise api.step.StepFailure('No CodeQL version provided')
  # Get the list of folders containing CodeQL databases.
  gsutil_list_result = api.gsutil.list(
      name="list of folders",
      url=DOWNLOAD_URL_PREFIX,
      stdout=api.raw_io.output(),
  )
  files = gsutil_list_result.stdout.splitlines()

  # Fetch the most recent CodeQL database upload directory.
  # TODO(flowerhack): There's a garbage file in the bucket left over from
  # earlier testing that, if deleted, would let us index on -1 instead of -2.
  # Obtain permissions to access that bucket directly, delete the file, then
  # update this script.
  directory_of_interest = ''
  try:
    directory_of_interest = files[-2].decode('UTF-8')
  except Exception as e:
    raise api.step.StepFailure(
        'Could not retrieve list of database folders from target bucket') from e

  # Extract directory basename.
  basename_of_directory_regex = r'gs://.*/(.*)/'
  result = re.match(basename_of_directory_regex, directory_of_interest)
  basename_of_directory = ''
  if result:
    basename_of_directory = result[1]
  else:
    raise api.step.StepFailure(
        'Extract basename of most recent CodeQL directory.')

  # Extract the correct DATETIME from that directory.
  datetime_from_folder_name_regex = (r'.*codeql\-(\d{4,}\-\d{2}\-\d{2}'
                                     r'\-\d{2}:\d{2}:\d{2})/')
  result = re.match(datetime_from_folder_name_regex, directory_of_interest)
  datetime = ''
  if result:
    datetime = result[1]
  else:
    raise api.step.StepFailure(
        'Extract DATETIME of most recent CodeQL directory.')

  # TODO(flowerhack): In a future CL, we'll make this more modular.
  # In particular, we'll fetch a list of "for database [x], what queries should
  # we run" from a config file & iterate through all databases [x] accordingly.
  # However, while we're doing v0 of these feature and ensuring everything works
  # end-to-end, let's just focus on d8 for now as it's a database of interest
  # and relatively easy to test against.
  db_name = "d8"

  unzipped_db_path = download_database(
      api=api,
      db_name=db_name,
      basename_of_directory=basename_of_directory,
      datetime=datetime)

  # TODO(flowerhack): Parallelize this + downloading of CodeQL databases via
  # the recipe futures API.
  source_dir = checkout_chromium(api)

  # Fetch CodeQL binary
  cipd_root = api.path.start_dir / 'cipd'
  with api.context(cwd=source_dir, env_suffixes={'PATH': [cipd_root]}):
    codeql_root = api.path.start_dir / 'codeql'
    ensure_file = api.cipd.EnsureFile().add_package(
        'infra/3pp/tools/codeql/${platform}', properties.codeql_version)
    api.cipd.ensure(codeql_root, ensure_file)
    codeql_path = codeql_root / 'codeql'

  # TODO(flowerhack): In a future version CL, we'll switch this to run
  # additional queries in //src/tools/codeql/queries. However, while we're doing
  # v0 of this feature and ensuring everything works end-to-end, let's make sure
  # the test query returns expected results in automation.
  query_path = source_dir.joinpath('tools', 'codeql', 'queries',
                                   'hello_world.ql')
  bqrs_out_dir = api.path.start_dir / 'bqrs_out_dir'
  api.file.ensure_directory("ensure bqrs_out_dir", bqrs_out_dir)
  bqrs_for_this_db_dir = bqrs_out_dir / db_name
  api.file.ensure_directory("ensure bqrs_out_dir/db_name", bqrs_for_this_db_dir)
  bqrs_out_file = bqrs_for_this_db_dir / 'hello_world.bqrs'

  api.step('Run a CodeQL query', [
      codeql_path, 'query', 'run', f'--database={unzipped_db_path}',
      f'--output={bqrs_out_file}', '--', query_path
  ])

  cur_date_str = api.time.utcnow().strftime('%Y-%m-%d-%H:%M:%S')
  cloud_folder_name = 'query-results-' + cur_date_str
  api.gsutil.upload(
      bqrs_out_dir,
      UPLOAD_BUCKET,
      cloud_folder_name,
      args=['-r'],
      link_name='Query results')


def GenTests(api: TEST_DEPS):
  yield api.test(
      'no files returned',
      api.properties(InputProperties(codeql_version='latest')),
      api.step_data('gsutil list of folders', stdout=api.raw_io.output('')),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'totally invalid list of files',
      api.properties(InputProperties(codeql_version='latest')),
      api.step_data(
          'gsutil list of folders',
          stdout=api.raw_io.output('gs://ageage/\ngs://ageagea/\n')),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'valid-ish list of files but no datetimes available',
      api.properties(InputProperties(codeql_version='latest')),
      api.step_data(
          'gsutil list of folders',
          stdout=api.raw_io.output('gs://foo/bar/\ngs://baz/bat/\n')),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'valid list of files',
      api.properties(InputProperties(codeql_version='latest')),
      api.step_data(
          'gsutil list of folders',
          stdout=api.raw_io.output(('gs://codeql-2024-08-30-12:11:11/bar/\n'
                                    'gs://codeql-2024-08-29-12:11:11/bat/\n'))),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'no CodeQL version provided',
      api.properties(InputProperties(codeql_version='')),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
