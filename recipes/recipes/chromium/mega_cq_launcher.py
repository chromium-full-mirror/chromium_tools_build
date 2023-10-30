# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""A small orch-like recipe used to launch builds in Chromium's "Mega" CQ

This recipe can be removed in favor of native CV behavior once it reaches
parity with what this recipe provides. That is, only after at least these
bugs are fixed:
https://crbug.com/1483511
https://crbug.com/1483516
https://crbug.com/1484829
https://crbug.com/1487672
"""
import datetime

from recipe_engine import post_process

DEPS = [
    'chromium',
    'depot_tools/gitiles',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/time',
]


def RunSteps(api):
  # Just curl the file to avoid needing a checkout. This means additions to
  # the mega CQ can't be tested in the CL that adds them via the mega CQ. We
  # assume this isn't a big deal.
  with api.step.nest('get mega_cq_bots.txt') as nest_step:
    mega_cq_bots_lines = api.gitiles.download_file(
        'https://chromium.googlesource.com/chromium/src',
        'infra/config/generated/cq-usage/mega_cq_bots.txt',
        branch=api.tryserver.gerrit_change_target_ref,
        step_name='read mega_cq_bots.txt',
        step_test_data=lambda: api.gitiles.test_api.make_encoded_file(
            'chromium/try/bot1\nchromium/try/bot2'),
    ).splitlines()
    nest_step.logs['bots'] = mega_cq_bots_lines

  trybots = []
  for line in mega_cq_bots_lines:
    project, bucket, builder = line.split('/')
    assert project == api.buildbucket.build.builder.project
    assert api.buildbucket.build.builder.bucket in [
        bucket,
        bucket + '.shadow',  # For led builds.
    ]
    trybots.append(builder)

  # Use a resource script so we can get vpython to fetch "pytz", which will
  # handle timezones for us.
  step_result = api.step(
      'get time',
      ['vpython3', api.resource('print_us_pac_time.py')],
      stdout=api.raw_io.output_text(),
      step_test_data=lambda: api.raw_io.test_api.stream_output_text(
          '2023-10-23 12:00:00.000000-07:00'),  # Noon on a Mon.
  )
  now = datetime.datetime.fromisoformat(step_result.stdout.strip())
  # We consider "peak" between 4:00 AM and 8:00 PM Pacific. 4:00 is quite
  # early, but that should avoid mega CQ runs triggered at 3:59 AM from
  # impacting normal CQ traffic during the real peak times.
  peak_start = datetime.datetime.combine(
      now.date(), datetime.time(hour=4), tzinfo=now.tzinfo)
  peak_end = datetime.datetime.combine(
      now.date(), datetime.time(hour=20), tzinfo=now.tzinfo)
  if now.weekday() < 5 and peak_start < now < peak_end:
    diff_s = (peak_end - now).seconds
    api.step('need to wait for off-peak hours; sleeping for %ds' % diff_s, None)
    api.time.sleep(diff_s)
  else:
    api.step('no sleep needed', None)

  # TODO(crbug.com/1227778): Finish the rest: trigger + collect the builds.


def GenTests(api):

  yield api.test(
      'sleep',
      api.chromium.try_build(),
      api.step_data(
          'get time',
          stdout=api.raw_io.output_text('2023-10-23 09:00:00.000000-07:00'),
      ),
      api.post_process(post_process.MustRun,
                       'need to wait for off-peak hours; sleeping for 39600s'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_sleep_before_peak',
      api.chromium.try_build(),
      api.step_data(
          'get time',
          stdout=api.raw_io.output_text('2023-10-23 23:00:00.000000-07:00'),
      ),
      api.post_process(post_process.MustRun, 'no sleep needed'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_sleep_after_peak',
      api.chromium.try_build(),
      api.step_data(
          'get time',
          stdout=api.raw_io.output_text('2023-10-23 01:00:00.000000-07:00'),
      ),
      api.post_process(post_process.MustRun, 'no sleep needed'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_sleep_weekend',
      api.chromium.try_build(),
      api.step_data(
          'get time',
          stdout=api.raw_io.output_text('2023-10-22 12:00:00.000000-07:00'),
      ),
      api.post_process(post_process.MustRun, 'no sleep needed'),
      api.post_process(post_process.DropExpectation),
  )
