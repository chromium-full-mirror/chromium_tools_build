# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json

from recipe_engine import post_process
from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import flakiness, tar
from RECIPE_MODULES.depot_tools import gsutil
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cipd,
    file,
    futures,
    json as json_module,
    path,
    raw_io,
    runtime,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  cipd: cipd.API
  file: file.API
  flakiness: flakiness.API
  futures: futures.API
  gsutil: gsutil.API
  json: json_module.API
  path: path.API
  raw_io: raw_io.API
  runtime: runtime.API
  step: step.API
  tar: tar.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  file: file.TEST_API
  flakiness: flakiness.TEST_API
  json: json_module.TEST_API
  path: path.TEST_API
  runtime: runtime.TEST_API
  step: step.TEST_API


def analyze_try_builder_test_history(api: DEPS, builder, gs_bucket,
                                     build_number, project, builder_bucket):
  """Query test history for the given builder, and upload the results to CIPD

  Args:
    builder: (str) name of the try builder
    gs_bucket: (str) name of gs bucket.
    build_number: (int) buildbucket build number of this recipe run.
    project: (str) project associated with builder. For example, "chromium"
    builder_bucket: (str) bucket associated with project & builder. For example,
      "ci" or "try"
  """
  with api.step.nest('analyze try builder {}.{}:{}'.format(
      project, builder_bucket, builder)):
    experimental = api.runtime.is_experimental
    source = api.flakiness.gs_source_template(experimental=experimental).format(
        project, builder_bucket, builder, build_number)

    # we're setting wildcard suffixes to ensure that the larger tables can
    # partition their results before the export to GS.
    gs_source = source + '{}_*.json'.format(builder)
    export_gs_path = 'gs://{}/{}'.format(gs_bucket, gs_source)
    cmd = [
        'vpython3',
        api.resource('query.py'),
        'history',
        '--builder={}'.format(builder),
        '--builder-bucket={}'.format(builder_bucket),
        '--project={}'.format(project),
        '--export-gs-path={}'.format(export_gs_path),
    ]
    api.step('query test history data', cmd)

    builder_folder = api.path.mkdtemp()
    # gsutil.download doesn't support appending args before the cp command.
    api.gsutil(['-m', 'cp', export_gs_path, builder_folder])

    files = api.file.glob_paths(
        'find all paritioned json files',
        builder_folder,
        builder + '_*.json',
        test_data=['[CLEANUP]/builder1_000.json'])

    builder_output_folder = api.path.cleanup_dir.joinpath(
        project, builder_bucket)
    api.file.ensure_directory('create dir', builder_output_folder)
    builder_output_file = builder_output_folder / '{}.json'.format(builder)
    cmd = ['vpython3', api.resource('query.py'), 'format']
    for f in files:
      cmd += ['--file', str(f)]
    cmd += ['--output-file', builder_output_file]
    api.step('format json', cmd)

    tar_filename = '{}.json.tar.gz'.format(builder)
    tar_gz = builder_output_folder / tar_filename
    pkg = api.tar.make_package(api.path.cleanup_dir, tar_gz, compression='gz')
    pkg.add_file(builder_output_file)
    pkg.tar('Create {}'.format(tar_filename))

    dest_gs_source = api.flakiness.gs_source_template(
        experimental=experimental).format(project, builder_bucket, builder,
                                          'latest') + tar_filename
    api.gsutil.upload(
        tar_gz,
        gs_bucket,
        dest_gs_source,
        args=['-Z'],
        name='copy {} to latest'.format(tar_filename))


def RunSteps(api: DEPS):
  # This recipe queries ResultDB via BigQuery directly to determine
  # historical test results for each try builder. The historical test results
  # are bundled and uploaded to CIPD such that try builders that check for
  # flakiness can fetch this file to determine new tests.
  cmd = [
      'vpython3',
      api.resource('query.py'), 'builders', '--output-file',
      api.json.output()
  ]
  result = api.step('search for try builders', cmd)
  builder_data = result.json.output

  build_number = api.buildbucket.build.number
  gs_bucket = api.flakiness.gs_bucket

  futures = []
  with api.step.nest('generating historical test data'):
    for b in builder_data:
      futures.append(
          api.futures.spawn(
              analyze_try_builder_test_history,
              api,
              b['builder_name'],
              gs_bucket,
              build_number,
              b['builder_project'],
              b['bucket'],
          ))

  for f in futures:
    f.result()


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.runtime(is_experimental=True),
      api.step_data(
          'search for try builders',
          api.json.output([{
              'builder_name': 'builder1',
              'builder_project': 'chromium',
              'bucket': 'try',
          }])),
      api.post_check(
          post_process.MustRun,
          'generating historical test data.analyze try builder '
          'chromium.try:builder1.query test history data'),
      api.post_check(post_process.MustRun,
                     ('generating historical test data.'
                      'analyze try builder chromium.try:builder1.'
                      'gsutil copy builder1.json.tar.gz to latest')),
      api.post_process(post_process.DropExpectation),
  )
