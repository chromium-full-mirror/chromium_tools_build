# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Runs the rts-suite-analysis tool against builder suite combinations"""

import datetime

from recipe_engine import post_process
from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

DEPS = [
    'recipe_engine/cipd',
    'recipe_engine/futures',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/step',
    'recipe_engine/time',
]

REJECTION_DATA_WINDOW = datetime.timedelta(weeks=12)
TEST_DURATION_DATA_WINDOW = datetime.timedelta(weeks=1)
# Processing 10% of 1w-worth test durations takes 7h on a 32-core bot.
TEST_DURATION_DATA_PERCENTAGE = 1

_CLOUD_PROJECT_ID = 'chrome-trooper-analytics'


def RunSteps(api):
  # Install rts-suite-analysis executables.
  exec_path = api.cipd.ensure_tool(
      'chromium/rts/rts-suite-analysis/${platform}', 'latest')

  # Fetch the dataset.
  # Ignore today because we might fetch incomplete data.
  yesterday = api.time.utcnow().date() - datetime.timedelta(days=1)
  rejections_dir, durations_dir = _fetch_model_data(
      api,
      exec_path,
      rejection_date_range=(
          yesterday - REJECTION_DATA_WINDOW,
          yesterday,
      ),
      duration_date_range=(
          yesterday - TEST_DURATION_DATA_WINDOW,
          yesterday,
      ))

  bq_cmd = [
      "bq", "query", "--project_id=" + _CLOUD_PROJECT_ID, "--format=json",
      "--max_rows=10", "--nouse_legacy_sql", builder_suite_query
  ]
  step_result = api.step(
      'get builder suites', bq_cmd, timeout=60, stdout=api.json.output())
  builder_suites = step_result.stdout

  for builder_suite in builder_suites:
    test_suite = builder_suite['test_suite']
    builder = builder_suite['builder']
    api.step(
        f'analyze {test_suite} on {builder}',
        [
            exec_path,
            'analyze',
            f'-rejections={rejections_dir}',
            f'-durations={durations_dir}',
            f'-builder={builder}',
            f'-testSuite={test_suite}',
        ],
    )

  return result_pb2.RawResult(status=common_pb.SUCCESS,)


def _fetch_model_data(api, exec_path, rejection_date_range,
                      duration_date_range):
  """Fetches the data for model creation.

  Returns:
    A tuple (rejections_dir, durations_dir) with path to the directories with
    rejections and durations respectively.
  """
  data_dir = api.path['cleanup'].join('rts-suite-analysis-model-data')
  rejections_dir = data_dir.join('rejections')
  durations_dir = data_dir.join('durations')

  futures = api.futures.wait([
      api.futures.spawn_immediate(
          api.step,
          'fetch rejections',
          [
              str(exec_path),
              'fetch-rejections',
              f'-out={rejections_dir}',
              '-ignore-file',
          ] + _date_range_flags(rejection_date_range),
      ),
      api.futures.spawn_immediate(
          api.step,
          'fetch durations',
          [
              str(exec_path),
              'fetch-durations',
              f'-frac={TEST_DURATION_DATA_PERCENTAGE / 100.0 :.3f}',
              f'-out={durations_dir}',
              '-ignore-file',
          ] + _date_range_flags(duration_date_range),
      ),
  ])

  # Check future's exception.
  for f in futures:
    f.result()

  return rejections_dir, durations_dir


def _date_range_flags(date_range):
  from_date, to_date = date_range
  return [
      f'-from={from_date.strftime("%Y-%m-%d")}',
      f'-to={to_date.strftime("%Y-%m-%d")}',
  ]


def GenTests(api):
  yield api.test(
      'basic',
      api.platform.name('linux'),
      api.platform.arch('intel'),
      api.platform.bits(64),
      api.step_data(
          'get builder suites',
          stdout=api.json.output([{
              'builder': 'linux-chromeos-rel',
              'test_suite': 'browser_tests'
          }, {
              'builder': 'android-nougat-x86-rel',
              'test_suite': 'chrome_public_test_apk'
          }])),
      api.post_process(post_process.MustRun,
                       'analyze browser_tests on linux-chromeos-rel'),
      api.post_process(
          post_process.MustRun,
          'analyze chrome_public_test_apk on android-nougat-x86-rel'),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )


builder_suite_query = '''
SELECT
  cq_builders.builder AS builder,
  (
    SELECT
      SPLIT(tag, ':')[OFFSET(1)]
    FROM UNNEST(s.request.tags) tag
    WHERE STARTS_WITH(tag, 'test_suite:')
  ) AS test_suite,
FROM `chromium-swarm.swarming.task_results_summary` s
  INNER JOIN `chrome-trooper-analytics.metrics.cq_builders` cq_builders
    ON (SELECT SPLIT(tag, ':')[OFFSET(1)] FROM UNNEST(s.request.tags) tag WHERE STARTS_WITH(tag, 'buildername:')) = cq_builders.builder
WHERE DATE(end_time) > DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
GROUP BY builder, test_suite
ORDER BY SUM(s.duration) DESC
'''
