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
from recipe_engine.engine_types import ResourceCost
from PB.recipe_engine.result import RawResult
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.buildbucket.proto.builds_service import (
    BatchResponse)

DEPS = [
    'chromium',
    'depot_tools/gitiles',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/cv',
    'recipe_engine/futures',
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

  def _run_bot(b):
    with api.step.nest('trigger ' + b):
      # Increase the default priority + timeout + expiration since we expect
      # mega CQ builds to take longer.
      per_build_expiration_s = 12 * 60 * 60
      per_build_timeout_s = 6 * 60 * 60

      def _make_req():
        req = api.buildbucket.schedule_request(
            b,
            project=project,
            bucket=bucket,
            priority=api.buildbucket.build.infra.swarming.priority + 10,
            tags=api.buildbucket.tags(mega_cq_build='1'))
        req.scheduling_timeout.FromSeconds(per_build_expiration_s)
        req.execution_timeout.FromSeconds(per_build_timeout_s)
        return req

      for i in range(1, 4):  # At most 2 retries per builder.
        # Buildbucket de-dupes when using the exact same request object. So need
        # to create a new one each time.
        req = _make_req()
        build = api.buildbucket.schedule([req],
                                         step_name='trigger (attempt %d)' %
                                         i)[0]
        api.cv.record_triggered_builds(build)
        result = api.buildbucket.collect_build(
            build.id,
            step_name='collect (attempt %d)' % i,
            # Mark the step as resource-free so it uncaps the amount of parallel
            # collects that can run. The step has minimal machine impact, so this
            # should be fine.
            cost=ResourceCost.zero(),
            timeout=per_build_expiration_s + per_build_timeout_s)
        if result.status == common_pb.SUCCESS:
          return result
      return result


  workers = []
  for b in trybots:
    workers.append(api.futures.spawn_immediate(_run_bot, b))
  api.futures.wait(workers)
  final_build_results = []
  for w in workers:
    final_build_results.append(w.result())

  total_success = 0
  total_failure = 0
  for result in final_build_results:
    if result.status == common_pb.SUCCESS:
      total_success += 1
    else:
      total_failure += 1
      step_result = api.step(result.builder.builder + ' failed', cmd=None)
      step_result.presentation.links[str(result.id)] = (
          api.buildbucket.build_url(build_id=result.id))
      step_result.presentation.status = api.step.FAILURE

  summary_md = '<br/>'.join([
      '%d builders succeeded' % total_success,
      '%d builders failed' % total_failure,
  ])
  overall_status = common_pb.FAILURE if total_failure else common_pb.SUCCESS
  return RawResult(status=overall_status, summary_markdown=summary_md)


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

  yield api.test(
      'pass_on_retry',
      api.chromium.try_build(),
      api.step_data(
          'get mega_cq_bots.txt.read mega_cq_bots.txt',
          api.gitiles.make_encoded_file('\n'.join([
              'chromium/try/green_bot',
              'chromium/try/flaky_bot',
          ]))),
      api.buildbucket.simulated_schedule_output(
          BatchResponse(responses=[{
              'schedule_build': {
                  'id': 100
              }
          }]),
          step_name='trigger flaky_bot.trigger (attempt 1)',
      ),
      api.buildbucket.simulated_collect_output(
          [
              api.buildbucket.ci_build_message(build_id=100, status='FAILURE'),
          ],
          step_name='trigger flaky_bot.collect (attempt 1)'),
      api.buildbucket.simulated_schedule_output(
          BatchResponse(responses=[{
              'schedule_build': {
                  'id': 101
              }
          }]),
          step_name='trigger flaky_bot.trigger (attempt 2)',
      ),
      api.buildbucket.simulated_collect_output(
          [
              api.buildbucket.ci_build_message(build_id=101, status='SUCCESS'),
          ],
          step_name='trigger flaky_bot.collect (attempt 2)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failing_bot',
      api.chromium.try_build(),
      api.step_data(
          'get mega_cq_bots.txt.read mega_cq_bots.txt',
          api.gitiles.make_encoded_file('\n'.join([
              'chromium/try/green_bot',
              'chromium/try/red_bot',
          ]))),
      api.buildbucket.simulated_schedule_output(
          BatchResponse(responses=[{
              'schedule_build': {
                  'id': 100
              }
          }]),
          step_name='trigger red_bot.trigger (attempt 1)',
      ),
      api.buildbucket.simulated_collect_output(
          [
              api.buildbucket.ci_build_message(build_id=100, status='FAILURE'),
          ],
          step_name='trigger red_bot.collect (attempt 1)'),
      api.buildbucket.simulated_schedule_output(
          BatchResponse(responses=[{
              'schedule_build': {
                  'id': 101
              }
          }]),
          step_name='trigger red_bot.trigger (attempt 2)',
      ),
      api.buildbucket.simulated_collect_output(
          [
              api.buildbucket.ci_build_message(build_id=101, status='FAILURE'),
          ],
          step_name='trigger red_bot.collect (attempt 2)'),
      api.buildbucket.simulated_schedule_output(
          BatchResponse(responses=[{
              'schedule_build': {
                  'id': 102
              }
          }]),
          step_name='trigger red_bot.trigger (attempt 3)',
      ),
      api.buildbucket.simulated_collect_output(
          [
              api.buildbucket.ci_build_message(build_id=102, status='FAILURE'),
          ],
          step_name='trigger red_bot.collect (attempt 3)'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )
