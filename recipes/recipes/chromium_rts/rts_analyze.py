# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Runs the rts-suite-analysis tool against builder suite combinations"""

import datetime
import re

from recipe_engine import post_process

from RECIPE_MODULES.build.attr_utils import attrib, attrs

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

DEPS = [
    'recipe_engine/cipd',
    'recipe_engine/file',
    'recipe_engine/futures',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/time',
]

# These are actually 4 week periods
REJECTION_DATA_MONTHS = 3
TEST_DURATION_DATA_WINDOW = datetime.timedelta(weeks=1)
# Processing 10% of 1w-worth test durations takes 7h on a 32-core bot.
TEST_DURATION_DATA_PERCENTAGE = 1

LOGGED_REJECTIONS = 100

_CLOUD_PROJECT_ID = 'chrome-trooper-analytics'


@attrs()
class SavingsAnalysis:
  builder = attrib(str)
  test_suite = attrib(str)
  recall = attrib(float)
  savings = attrib(float)

  def report(self):
    return f'{self.builder}:{self.test_suite} {self.recall}%, {self.savings}%'


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
      duration_date_range=(
          yesterday - TEST_DURATION_DATA_WINDOW,
          yesterday,
      ))

  bq_cmd = [
      "bq", "query", "--project_id=" + _CLOUD_PROJECT_ID, "--format=json",
      "--max_rows=10000", "--nouse_legacy_sql", builder_suite_query
  ]
  step_result = api.step(
      'get builder suites', bq_cmd, timeout=60, stdout=api.json.output())
  builder_suites = step_result.stdout

  futures = []
  for builder_suite in builder_suites:
    test_suite = builder_suite['test_suite']
    builder = builder_suite['builder']
    futures.append(
        api.futures.spawn_immediate(_analyze_builder_suite, api, builder,
                                    test_suite, rejections_dir, durations_dir,
                                    exec_path))

  analyses = []
  # Check future's exception.
  for f in futures:
    result = f.result()
    if result:
      analyses.append(result)

  # Sort by recall. There will be lots of low savings but it should be easy
  # to scan for the best candidates
  analyses.sort(
      key=lambda analysis: (analysis.recall, analysis.savings), reverse=True)

  summary = 'Analysis Summary (recall, savings):\n\n' + '\n\n'.join(
      analysis.report() for analysis in analyses)
  return result_pb2.RawResult(
      status=common_pb.SUCCESS, summary_markdown=summary[:4000])


def _analyze_builder_suite(api, builder, test_suite, rejections_dir,
                           durations_dir, exec_path):
  # Request 1 CPU core (1000 millicores) and 2 GiB RAM (2048 MiB) per step.
  # On a 60-core c2-standard-60 VM (60,000 millicores, ~240 GiB RAM), the recipe
  # engine's ResourceWaiter automatically throttles execution to at most 60
  # concurrent processes (60,000 / 1,000 = 60).
  step_result = api.step(
      f'analyze {test_suite} on {builder}', [
          exec_path,
          'analyze',
          f'-rejections={rejections_dir}',
          f'-durations={durations_dir}',
          f'-builder={builder}',
          f'-testSuite={test_suite}',
          f'-log-furthest={LOGGED_REJECTIONS}',
      ],
      stdout=api.raw_io.output_text(),
      cost=api.step.ResourceCost(cpu=1000))
  match = re.search(r'(\d+\.\d+)%\s*\|\s*<?(\d+\.\d+)%', step_result.stdout)
  if not match:
    # No summary table implies something went wrong with the analysis
    step_result.presentation.status = api.step.FAILURE
    step_result.presentation.step_text = step_result.stdout
  else:
    recall = float(match.group(1))
    savings = float(match.group(2))
    step_result.presentation.step_text = (
        f'Recall {recall} Savings {savings}\n' + step_result.stdout)
    return SavingsAnalysis(
        builder=builder,
        test_suite=test_suite,
        recall=recall,
        savings=savings,
    )


def _fetch_model_data(api, exec_path, duration_date_range):
  """Fetches the data for model creation.

  Returns:
    A tuple (rejections_dir, durations_dir) with path to the directories with
    rejections and durations respectively.
  """
  data_dir = api.path.cleanup_dir / 'rts-suite-analysis-model-data'
  rejections_dir = data_dir / 'rejections'
  durations_dir = data_dir / 'durations'

  # Using -append flag doesn't clean the rejections folder so ensure it's
  # cleaned before the fetches
  api.file.rmcontents('clean rejections', rejections_dir)

  fetches = []
  end = api.time.utcnow().date() - datetime.timedelta(days=1)

  for _ in range(REJECTION_DATA_MONTHS):
    start = end - datetime.timedelta(weeks=4)

    fetches.append(
        api.futures.spawn_immediate(
            api.step,
            'fetch rejections (fast)',
            [
                str(exec_path),
                'fetch-rejections-fast',
                '-ignore-file',
                '-append',
                f'-out={rejections_dir}',
            ] + _date_range_flags((start, end)),
        ))

    end = start

  fetches.append(
      api.futures.spawn_immediate(
          api.step,
          'fetch durations',
          [
              str(exec_path),
              'fetch-durations',
              f'-frac={TEST_DURATION_DATA_PERCENTAGE / 100.0 :.3f}',
              f'-out={durations_dir}',
          ] + _date_range_flags(duration_date_range),
      ))

  with api.futures.iwait(fetches) as itr:
    for future in itr:
      future.result()

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
          }, {
              'builder': 'mac',
              'test_suite': 'suite_fails_to_complete'
          }])),
      api.step_data(
          'analyze browser_tests on linux-chromeos-rel',
          stdout=api.raw_io.output_text('''
Rejection:
     Most affected test: +Inf distance
     https://chromium-review.googlesource.com/c/4398410/4
       //chrome/browser/media/encrypted_media_browsertest.cc
     Failed and not selected tests:
       - builder:win-rel | os:Windows-10-19045 | test_suite:browser_tests
         in //chrome/browser/media/encrypted_media_browsertest.cc
           ninja://chrome/test:browser_tests/MediaFoundationEncryptedMediaTest.Playback_EncryptedAudioCbcs_MediaTypeUnsupported
 ChangeRecall | Savings
 ----------------------
  99.16%      |   8.37% 
 based on 837 rejections, 862565 test failures, 2 years 52 days 20 hours 22 minutes 37 seconds testing time
 ''')),
      api.step_data(
          'analyze chrome_public_test_apk on android-nougat-x86-rel',
          stdout=api.raw_io.output_text('''
Rejection:
     Most affected test: +Inf distance
     https://chromium-review.googlesource.com/c/4398410/4
       //chrome/file.cc
     Failed and not selected tests:
       - builder:android-nougat-x86-rel | os:android | test_suite:chrome_public_test_apk
         in //chrome/file.cc
           ninja://chrome/test:chrome_public_test_apk/test.case
 ChangeRecall | Savings
 ----------------------
  100.00%     |  90.37% 
 based on 837 rejections, 862565 test failures, 2 years 52 days 20 hours 22 minutes 37 seconds testing time
 ''')),
      api.step_data(
          'analyze suite_fails_to_complete on mac',
          stdout=api.raw_io.output_text('Failed to run')),
      api.post_process(post_process.MustRun,
                       'analyze browser_tests on linux-chromeos-rel'),
      api.post_process(
          post_process.MustRun,
          'analyze chrome_public_test_apk on android-nougat-x86-rel'),
      api.post_process(
          post_process.SummaryMarkdown,
          'Analysis Summary (recall, savings):\n\nandroid-nougat-x86-rel:chrome_public_test_apk 100.0%, 90.37%\n\nlinux-chromeos-rel:browser_tests 99.16%, 8.37%'
      ),
      api.post_process(post_process.StepCommandContains,
                       'analyze suite_fails_to_complete on mac',
                       ['-log-furthest=100']),
      api.post_process(post_process.StepCommandContains,
                       'fetch rejections (fast)',
                       ['-from=2012-04-15', '-to=2012-05-13']),
      api.post_process(post_process.StepCommandContains,
                       'fetch rejections (fast) (2)',
                       ['-from=2012-03-18', '-to=2012-04-15']),
      api.post_process(post_process.StepCommandContains,
                       'fetch rejections (fast) (3)',
                       ['-from=2012-02-19', '-to=2012-03-18']),
      api.post_check(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )


builder_suite_query = '''
SELECT
  (SELECT v.value FROM tr.variant AS v WHERE v.key = 'builder') AS builder,
  (SELECT v.value FROM tr.variant AS v WHERE v.key = 'test_suite') AS test_suite,
FROM chrome-luci-data.chromium.try_test_results tr
  INNER JOIN `chrome-trooper-analytics.metrics.cq_builders` cq_builders
    ON (SELECT v.value FROM tr.variant AS v WHERE v.key = 'builder') = cq_builders.builder
WHERE DATE(tr.partition_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
GROUP BY builder, test_suite
ORDER BY COUNT(*) DESC
'''
