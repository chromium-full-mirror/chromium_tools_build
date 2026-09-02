# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from PB.go.chromium.org.luci.buildbucket.proto.builds_service import (
    BatchResponse)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_mega_cq, chromium_orchestrator
from RECIPE_MODULES.depot_tools import gitiles, tryserver
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    json,
    properties,
    raw_io,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_mega_cq: chromium_mega_cq.API
  chromium_orchestrator: chromium_orchestrator.API
  gitiles: gitiles.API
  json: json.API
  properties: properties.API
  raw_io: raw_io.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  gitiles: gitiles.TEST_API
  json: json.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API


def RunSteps(api: DEPS):
  trybots = api.chromium_mega_cq.read_bots_file(
      'https://chromium.googlesource.com/chromium/src',
      'infra/config/generated/cq-usage/mega_cq_bots.txt',
  )

  api.chromium_mega_cq.sleep_until_off_peak()

  gerrit_change = None
  if api.tryserver.is_tryserver:
    gerrit_change = api.tryserver.gerrit_change
  result, _ = api.chromium_mega_cq.trigger_and_collect_bots(
      trybots, gerrit_change=gerrit_change)
  return result


def GenTests(api: TEST_DEPS):
  yield api.test(
      'ci_bot',
      api.chromium.ci_build(),
      api.step_data(
          'get time',
          stdout=api.raw_io.output_text('2023-10-23 23:00:00.000000-07:00'),
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'sleep',
      api.chromium.try_build(),
      api.step_data(
          'get time',
          stdout=api.raw_io.output_text('2023-10-23 09:00:00.000000-07:00'),
      ),
      api.post_process(
          post_process.MustRun,
          'need to wait for off-peak hours; sleeping for 11:00:00'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'sleep_deleted_cl',
      api.chromium.try_build(),
      api.step_data(
          'get time',
          stdout=api.raw_io.output_text('2023-10-23 09:00:00.000000-07:00'),
      ),
      api.override_step_data('gerrit changes', api.json.output([])),
      api.post_process(
          post_process.SummaryMarkdown,
          'CL no longer present on Gerrit',
      ),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
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
      'failed_build_with_led',
      api.properties(
          **{
              '$recipe_engine/led': {
                  'shadowed_bucket': 'bucket',
                  'led_run_id': 'chromium/led/user_google.com/88a27cab21a8ad2',
                  'rbe_cas_input': {
                      'cas_instance':
                          'projects/chromium-swarm/instances/default_instance',
                      'digest': {
                          'hash':
                              'd103ae94b838a760cbe22514bc43cb98fb2888226a37500bcbd661ab2fd2c502',
                          'size_bytes':
                              752
                      }
                  },
              }
          }),
      api.chromium.try_build(),
      api.step_data(
          'get mega_cq_bots.txt.read mega_cq_bots.txt',
          api.gitiles.make_encoded_file('\n'.join([
              'chromium/try/green_bot',
              'chromium/try/red_bot',
          ]))),
      api.buildbucket.simulated_collect_output(
          [
              api.buildbucket.ci_build_message(
                  build_id=87654321, status='FAILURE'),
          ],
          step_name='trigger red_bot.collect (attempt 1)'),
      api.buildbucket.simulated_collect_output(
          [
              api.buildbucket.ci_build_message(
                  build_id=87654321, status='FAILURE'),
          ],
          step_name='trigger red_bot.collect (attempt 2)'),
      api.buildbucket.simulated_collect_output(
          [
              api.buildbucket.ci_build_message(
                  build_id=87654321, status='FAILURE'),
          ],
          step_name='trigger red_bot.collect (attempt 3)'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
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

  def check_cannot_outlive_parent(check, steps, step_name):
    if not check(step_name in steps, f'step {step_name} was run'):
      return  # pragma: no cover

    req = api.json.loads(steps[step_name].logs['request'])
    can_outlive_parent = (
        req["requests"][0]["scheduleBuild"].get("canOutliveParent"))
    check(can_outlive_parent == 'NO')

  yield api.test(
      'triggered-builds-cannot-outlive-parent',
      # Experiment is always set in production and must be set for
      # api.buildbucket.schedule_request to correctly set canOutliveParent in
      # the request
      api.chromium.try_build(experiments=['luci.buildbucket.parent_tracking']),
      api.post_check(check_cannot_outlive_parent,
                     'trigger bot1.trigger (attempt 1)'),
      api.post_process(post_process.DropExpectation),
  )
