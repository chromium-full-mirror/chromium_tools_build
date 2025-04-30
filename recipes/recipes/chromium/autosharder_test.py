# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from collections.abc import Iterable
import datetime

from recipe_engine import post_process
from recipe_engine.recipe_api import RecipeApi

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2

DEPS = [
    'chromium_checkout',
    'chromium_gerrit_utils',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/futures',
    'recipe_engine/led',
    'recipe_engine/json',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/swarming',
]


def RunSteps(api: RecipeApi):
  """Tests a autosharder generated CL to verify the shards work as expected."""
  api.gclient.set_config('chromium')

  builder_suites, revision = _get_new_shardings(api)

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

    for _, future in futures.items():
      future.result()


def _get_new_shardings(api: RecipeApi):
  update_result = api.chromium_checkout.ensure_checkout()
  exceptions_file = api.chromium_checkout.source_dir.joinpath(
      'infra', 'config', 'targets', 'autoshard_exceptions.json')
  shard_exceptions = api.m.file.read_json(
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

  previous_shard_exceptions = api.m.file.read_json(
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
    api: RecipeApi,
    builder_name: str,
    suites: Iterable[str],
    revision: str,
    current_change: common_pb.GerritChange,
    control_change: common_pb.GerritChange,
):

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
        _compare_shards(
            api,
            builder_name,
            suite,
            pre_shard_tasks,
            minutes_by_preshard_task_id,
            post_shard_tasks,
            minutes_by_postshard_task_id,
        )


def _parse_ts(ts: str) -> datetime.datetime:
  return datetime.datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S.%fZ')


def _get_shards(
    api: RecipeApi,
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
    api: RecipeApi,
    builder_name: str,
    suite: str,
    pre_shard_tasks: list[dict],
    minutes_by_preshard_task_id: dict[str, float],
    post_shard_tasks: list[dict],
    minutes_by_postshard_task_id: dict[str, float],
):
  # Infer the sharding count from the number of shard launched
  preshard_count = len(pre_shard_tasks)
  postshard_count = len(post_shard_tasks)

  step_result = api.step.empty(
      f'{builder_name}:{suite} preshard task count: {preshard_count} vs postshard {postshard_count}'
  )

  # Add links to tasks
  for task_id, minutes in minutes_by_preshard_task_id.items():
    display = f'pre_shard_task {task_id} ({round(minutes, 2)} minutes)'
    link = f'{api.swarming.current_server}/task?id={task_id}'
    step_result.presentation.links[display] = link

  for task_id, minutes in minutes_by_postshard_task_id.items():
    display = f'post_shard_task {task_id} ({round(minutes, 2)} minutes)'
    link = f'{api.swarming.current_server}/task?id={task_id}'
    step_result.presentation.links[display] = link

  # Store task detailed info for debugging
  step_result.presentation.logs['pre_shard_tasks'] = api.json.dumps(
      pre_shard_tasks, indent=2)
  step_result.presentation.logs['post_shard_tasks'] = api.json.dumps(
      post_shard_tasks, indent=2)


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
