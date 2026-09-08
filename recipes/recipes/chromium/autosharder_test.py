# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from collections.abc import Iterable
import datetime
import textwrap

from recipe_engine import post_process
from recipe_engine.recipe_api import RecipeApi

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2

MAX_OVERHEAD = 5.0
MAX_OVERHEAD_PERCENTAGE = 0.5
TARGET_RUNTIME = 15.0

SKIP_FOOTER = 'Autosharder-Skip'

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_checkout, chromium_gerrit_utils
from RECIPE_MODULES.depot_tools import (
    bot_update,
    gclient,
    git,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    file,
    futures,
    json,
    led,
    raw_io,
    step,
    swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium_checkout: chromium_checkout.API
  chromium_gerrit_utils: chromium_gerrit_utils.API
  context: context.API
  file: file.API
  futures: futures.API
  gclient: gclient.API
  git: git.API
  json: json.API
  led: led.API
  raw_io: raw_io.API
  step: step.API
  swarming: swarming.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  buildbucket: buildbucket.TEST_API
  chromium_checkout: chromium_checkout.TEST_API
  context: context.TEST_API
  file: file.TEST_API
  git: git.TEST_API
  json: json.TEST_API
  led: led.TEST_API
  raw_io: raw_io.TEST_API
  step: step.TEST_API
  swarming: swarming.TEST_API


def RunSteps(api: RecipeApi):
  """Tests a autosharder generated CL to verify the shards work as expected."""
  api.gclient.set_config('chromium')

  builder_suites, revision = _get_new_shardings(api)

  footers = api.tryserver.get_footer(SKIP_FOOTER)
  skip_builders = set()
  for f in footers:
    skip_builders.update(f.split(','))
  builder_suites = {
      b: s for b, s in builder_suites.items() if b not in skip_builders
  }

  if not builder_suites:
    return result_pb2.RawResult(
        status=common_pb.SUCCESS, summary_markdown='No changes found')

  with api.context(cwd=api.chromium_checkout.source_dir):
    current_change = api.tryserver.gerrit_change

    # Changing the DEPS file can cause the "without patch" phase to run
    # on the control_change but not the current change. Since we only
    # look at "with patch" this should be okay but is wasted.
    control_change, _ = api.chromium_gerrit_utils.create_temp_cl(
        'DEPS',
        'autosharder',
        [f'Created for {current_change.change}'],
    )

    futures = {}

    for builder, suites in builder_suites.items():
      futures[builder] = api.futures.spawn_immediate(
          _test_builder,
          api,
          builder,
          suites,
          revision,
          current_change,
          control_change,
      )

    failed_builders = []
    for builder, future in futures.items():
      builder_success = future.result()
      if not builder_success:
        failed_builders.append(builder)

    if failed_builders:
      return result_pb2.RawResult(
          status=common_pb.FAILURE,
          summary_markdown=textwrap.dedent(
              f"""Failed to verify builders. To bypass builder checks, add:
              {SKIP_FOOTER}:{','.join(failed_builders)}
              footer to the CL description.
              """))
    return result_pb2.RawResult(status=common_pb.SUCCESS)


def _get_new_shardings(api: DEPS) -> tuple[dict[str, list[str]], str]:
  update_result = api.chromium_checkout.ensure_checkout()
  exceptions_file = api.chromium_checkout.source_dir.joinpath(
      'infra', 'config', 'autoshard_exceptions.json')
  shard_exceptions = api.file.read_json(
      'read current exceptions file',
      exceptions_file,
      test_data={
          'foo-group': {
              'foo-builder': {
                  'foo-suite': {
                      'shards': 1,
                      'try_builder': 'bar-builder',
                  }
              }
          }
      })
  with api.context(cwd=update_result.checkout_dir):
    api.bot_update.deapply_patch(update_result)
  revision = api.git(
      'rev-parse',
      'HEAD',
      stdout=api.raw_io.output_text(),
      step_test_data=lambda: api.raw_io.test_api.stream_output_text('deadbeef')
  ).stdout.strip()

  previous_shard_exceptions = api.file.read_json(
      'read previous exceptions file',
      exceptions_file,
      test_data={
          'foo-group': {
              'foo-builder': {
                  'foo-suite': {
                      'shards': 2,
                      'try_builder': 'bar-builder',
                  }
              }
          }
      })

  builder_suites = {}
  for builder_group, waterfall_builders in shard_exceptions.items():
    for waterfall_builder, test_suites in waterfall_builders.items():
      for test_suite, sharding_info in test_suites.items():
        try_builder = sharding_info['try_builder']
        new_sharding = sharding_info['shards']

        previous_sharding = previous_shard_exceptions.get(
            builder_group, {}).get(waterfall_builder,
                                   {}).get(test_suite, {}).get('shards', -1)
        if new_sharding != previous_sharding:
          builder_suites.setdefault(try_builder, []).append(test_suite)
  step_result = api.step.empty('changed shardings')
  step_result.presentation.logs['builder_suites'] = api.json.dumps(
      builder_suites, indent=2)
  return builder_suites, revision


def _test_builder(
    api: DEPS,
    builder_name: str,
    suites: Iterable[str],
    revision: str,
    current_change: common_pb.GerritChange,
    control_change: common_pb.GerritChange,
) -> bool:

  suite_list = ', '.join(suites)
  with api.step.nest(f'Test {suite_list} on {builder_name} new sharding'):

    def _launch_build(change, step_name):
      with api.step.nest(step_name):
        change_url = (f'https://{change.host}/c/{change.project}'
                      f'/+/{change.change}/{change.patchset}')
        rev_url = f'https://chromium.googlesource.com/chromium/src/+/{revision}'
        led_result = api.led('get-builder', '-adjust-priority', '0',
                             f'chromium/try/{builder_name}')
        led_result = led_result.then('edit-gerrit-cl', change_url)
        led_result = led_result.then('edit-gitiles-commit', '-ref',
                                     'refs/heads/main', rev_url)
        led_result = led_result.then('launch')

      return led_result.launch_result.build_id

    pre_shard_build_id = _launch_build(control_change, 'launch pre sharded')
    post_shard_build_id = _launch_build(current_change, 'launch post sharded')
    pre_shard_build_number = api.buildbucket.get(pre_shard_build_id).number
    post_shard_build_number = api.buildbucket.get(post_shard_build_id).number

    # Wait for the builds to finish
    api.buildbucket.collect_builds([pre_shard_build_id, post_shard_build_id],
                                   step_name='waiting for builds to complete',
                                   timeout=21600)

    build_success = True
    for suite in suites:
      with api.step.nest(f'analyze {suite}'):
        pre_shard_tasks, minutes_by_preshard_task_id = _get_shards(
            api,
            'list pre shard tasks',
            suite,
            pre_shard_build_number,
        )
        post_shard_tasks, minutes_by_postshard_task_id = _get_shards(
            api,
            'list post shard tasks',
            suite,
            post_shard_build_number,
        )
        shard_succeeded = _compare_shards(
            api,
            builder_name,
            suite,
            pre_shard_tasks,
            minutes_by_preshard_task_id,
            post_shard_tasks,
            minutes_by_postshard_task_id,
        )
        if not shard_succeeded:
          build_success = False

  return build_success


def _parse_ts(ts: str) -> datetime.datetime:
  return datetime.datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S.%fZ')


def _get_shards(
    api: DEPS,
    step_name: str,
    suite: str,
    build_number: int,
) -> tuple[list[dict], dict[str, float]]:
  shard_tasks = api.swarming.list_tasks(
      step_name,
      tags=[
          'test_phase:with patch',
          f'test_suite:{suite}',
          f'buildnumber:{build_number}',
      ])
  minutes_by_task_id = {
      t['task_id']:
          float((_parse_ts(t['completed_ts']) -
                 _parse_ts(t['started_ts'])).seconds) / 60.0
      for t in shard_tasks
  }
  return shard_tasks, minutes_by_task_id


def _compare_shards(
    api: DEPS,
    builder_name: str,
    suite: str,
    pre_shard_tasks: list[dict],
    minutes_by_preshard_task_id: dict[str, float],
    post_shard_tasks: list[dict],
    minutes_by_postshard_task_id: dict[str, float],
) -> bool:
  # Get the max shard length which will be the long pole and will determine
  # the suite's performance in CQ
  max_preshard = max(minutes_by_preshard_task_id.values())
  max_postshard = max(minutes_by_postshard_task_id.values())

  # Infer the sharding count from the number of shard launched
  preshard_count = len(pre_shard_tasks)
  postshard_count = len(post_shard_tasks)

  summary_text = textwrap.dedent(f"""\
    Suite comparison:
    Max pre-sharded = {round(max_preshard, 2)} minutes
    Max post-sharded = {round(max_postshard, 2)} minutes

    Pre-shard_count = {preshard_count}
    Post-shard_count = {postshard_count}
    """)

  verification_status = api.step.SUCCESS
  if preshard_count < postshard_count:
    # Assume the change is perfectly representative of the overhead and
    # that any increase is due to shard overhead.
    # Overhead per shard = total increase divided by the number of new shards
    overhead = ((max_postshard * postshard_count) -
                (max_preshard * preshard_count)) / (
                    postshard_count - preshard_count)
    if overhead > MAX_OVERHEAD:
      verification_status = api.step.FAILURE
      summary_text = (
          f'Actual overhead ({round(overhead, 2)} minutes) is above the max '
          f'({MAX_OVERHEAD} minutes).\n{summary_text}')
    if overhead > max_postshard * MAX_OVERHEAD_PERCENTAGE:
      verification_status = api.step.FAILURE
      summary_text += (
          f'Actual overhead ({round(overhead, 2)} minutes) is more than '
          f'{MAX_OVERHEAD_PERCENTAGE * 100}% of the new runtime '
          f'({round(max_postshard, 2)} minutes).\n{summary_text}')
  elif max_postshard > TARGET_RUNTIME:
    # Decreasing the shard count, just make sure it still hits the target
    verification_status = api.step.FAILURE
    summary_text = ('Decreased sharding does not meet the target runtime.\n'
                    f'{summary_text}')

  step_result = api.step.empty(
      'analysis',
      step_text=summary_text,
      status=verification_status,
      raise_on_failure=False,
  )

  # Store task detailed info for debugging
  step_result.presentation.logs['pre_shard_tasks'] = api.json.dumps(
      pre_shard_tasks, indent=2)
  step_result.presentation.logs['post_shard_tasks'] = api.json.dumps(
      post_shard_tasks, indent=2)

  # Add links to tasks
  for task_id, minutes in minutes_by_preshard_task_id.items():
    display = f'pre_shard_task {task_id} ({round(minutes, 2)} minutes)'
    link = f'{api.swarming.current_server}/task?id={task_id}'
    step_result.presentation.links[display] = link

  for task_id, minutes in minutes_by_postshard_task_id.items():
    display = f'post_shard_task {task_id} ({round(minutes, 2)} minutes)'
    link = f'{api.swarming.current_server}/task?id={task_id}'
    step_result.presentation.links[display] = link

  return verification_status == api.step.SUCCESS


def GenTests(api: RecipeApi):
  yield api.test(
      'basic',
      api.buildbucket.try_build(),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_changes',
      api.buildbucket.try_build(),
      api.override_step_data(
          'read previous exceptions file',
          api.file.read_json({
              'foo-group': {
                  'foo-builder': {
                      'foo-suite': {
                          'shards': 1,
                          'try_builder': 'bar-builder',
                      }
                  }
              }
          })),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'footer_skips_analysis',
      api.buildbucket.try_build(),
      api.step_data('parse description',
                    api.json.output({SKIP_FOOTER: ['bar-builder']})),
      api.post_process(post_process.SummaryMarkdown, 'No changes found'),
      api.post_process(post_process.DropExpectation),
  )

  # The original shard only takes 1 second but the new sharding takes 5 minutes
  # That means 1 second of work is now being done in 10 mins. Less the actual
  # work of .5 seconds per shard is ~9.98 minutes
  yield api.test(
      'over_max_overhead',
      api.buildbucket.try_build(),
      api.override_step_data(
          'Test foo-suite on bar-builder new sharding.analyze foo-suite.list pre shard tasks',
          api.json.output([{
              'task_id': 12341234,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:00:01.0Z',
          }])),
      api.override_step_data(
          'Test foo-suite on bar-builder new sharding.analyze foo-suite.list post shard tasks',
          api.json.output([{
              'task_id': 12341235,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:05:00.0Z',
          }, {
              'task_id': 12341236,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:05:00.0Z',
          }])),
      api.post_process(
          post_process.StepTextContains,
          'Test foo-suite on bar-builder new sharding.analyze foo-suite.analysis',
          [
              'Actual overhead (9.98 minutes) is more than 50.0% of the new runtime (5.0 minutes).',
              'Actual overhead (9.98 minutes) is above the max (5.0 minutes).',
              'Pre-shard_count = 1',
              'Post-shard_count = 2',
          ]),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  # 2 shards running at 1 minute each. Assume an overhead of 30 seconds and 30
  # seconds of actual testing. The shard run times at 3 shards should be 20
  # seconds of testing and 30 seconds of overhead each. The total overhead
  # (.5 minutes) is acceptable but the 50% is not given that we are targeting
  # 15 mins this should realistically only catch shards that aren't actually
  # filtering and duplicating testing instead.
  yield api.test(
      'over_overhead',
      api.buildbucket.try_build(),
      api.override_step_data(
          'Test foo-suite on bar-builder new sharding.analyze foo-suite.list pre shard tasks',
          api.json.output([{
              'task_id': 12341234,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:01:00.0Z',
          }, {
              'task_id': 12341235,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:01:00.0Z',
          }])),
      api.override_step_data(
          'Test foo-suite on bar-builder new sharding.analyze foo-suite.list post shard tasks',
          api.json.output([{
              'task_id': 12341236,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:00:50.0Z',
          }, {
              'task_id': 12341237,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:00:50.0Z',
          }, {
              'task_id': 12341238,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:00:50.0Z',
          }])),
      api.post_process(
          post_process.StepTextContains,
          'Test foo-suite on bar-builder new sharding.analyze foo-suite.analysis',
          [
              'Actual overhead (0.5 minutes) is more than 50.0% of the new runtime (0.83 minutes).',
              'Pre-shard_count = 2',
              'Post-shard_count = 3',
          ]),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  # 2 shards running at 1 minute each. Assume an overhead of 30 seconds and 30
  # seconds of actual testing. The shard run times at 5 shards should be 12
  # seconds of testing and 30 seconds of overhead each. The total overhead
  # (30 s) is acceptable but the overhead being > 50% of 42 seconds is not.
  yield api.test(
      'over_overhead_multiple_new_shards',
      api.buildbucket.try_build(),
      api.override_step_data(
          'Test foo-suite on bar-builder new sharding.analyze foo-suite.list pre shard tasks',
          api.json.output([{
              'task_id': 12341234,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:01:00.0Z',
          }, {
              'task_id': 12341235,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:01:00.0Z',
          }])),
      api.override_step_data(
          'Test foo-suite on bar-builder new sharding.analyze foo-suite.list post shard tasks',
          api.json.output([{
              'task_id': 12341236,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:00:42.0Z',
          }, {
              'task_id': 12341237,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:00:42.0Z',
          }, {
              'task_id': 12341238,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:00:42.0Z',
          }, {
              'task_id': 12341239,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:00:42.0Z',
          }, {
              'task_id': 12341240,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:00:42.0Z',
          }])),
      api.post_process(
          post_process.StepTextContains,
          'Test foo-suite on bar-builder new sharding.analyze foo-suite.analysis',
          [
              'Actual overhead (0.5 minutes) is more than 50.0% of the new runtime (0.7 minutes).',
              'Pre-shard_count = 2',
              'Post-shard_count = 5',
          ]),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'decrease_does_not_hit_target',
      api.buildbucket.try_build(),
      api.override_step_data(
          'Test foo-suite on bar-builder new sharding.analyze foo-suite.list pre shard tasks',
          api.json.output([{
              'task_id': 12341234,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:00:01.0Z',
          }, {
              'task_id': 12341236,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:15:01.0Z',
          }])),
      api.override_step_data(
          'Test foo-suite on bar-builder new sharding.analyze foo-suite.list post shard tasks',
          api.json.output([{
              'task_id': 12341235,
              'started_ts': '2025-01-01T01:00:00.0Z',
              'completed_ts': '2025-01-01T01:15:01.0Z',
          }])),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
