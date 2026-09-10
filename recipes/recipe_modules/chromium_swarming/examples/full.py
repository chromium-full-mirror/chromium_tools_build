# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
  chromium_swarming as chromium_swarming_module,
  code_coverage,
  swarming_client,
  test_utils,
)
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  file,
  json,
  path,
  properties,
  raw_io,
  runtime,
  step,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_swarming: chromium_swarming_module.API
  code_coverage: code_coverage.API
  file: file.API
  gclient: gclient.API
  json: json.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  runtime: runtime.API
  step: step.API
  swarming: swarming.API
  swarming_client: swarming_client.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_swarming: chromium_swarming_module.TEST_API
  code_coverage: code_coverage.TEST_API
  json: json.TEST_API
  path: path.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  runtime: runtime.TEST_API
  swarming: swarming.TEST_API
  test_utils: test_utils.TEST_API


from recipe_engine.recipe_api import Property
from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_swarming
from RECIPE_MODULES.build.chromium_tests.steps import ResultDB

PROPERTIES = {
  'platforms': Property(default=('win',)),
  'custom_trigger_script': Property(default=False),
  'show_outputs_ref_in_collect_step': Property(default=True),
  'gtest_task': Property(default=False),
  'isolated_script_task': Property(default=False),
  'merge': Property(default=None),
  'trigger_script': Property(default=None),
  'named_caches': Property(default=None),
  'service_account': Property(default=None),
  'wait_for_tasks': Property(default=None),
  'realm': Property(default=None),
  'resultdb_spec': Property(default={}),
  'raw_cmd': Property(default=['hello_world.exe']),
}


def RunSteps(
  api: DEPS,
  platforms,
  custom_trigger_script,
  show_outputs_ref_in_collect_step,
  gtest_task,
  isolated_script_task,
  merge,
  trigger_script,
  named_caches,
  service_account,
  wait_for_tasks,
  realm,
  resultdb_spec,
  raw_cmd,
):
  # Checkout swarming client.
  api.swarming_client.checkout('main')

  api.gclient.set_config('chromium')
  api.chromium_checkout.ensure_checkout()

  # Configure swarming modules (this is optional).
  api.chromium_swarming.add_default_tag('builder_group:tryserver')
  api.chromium_swarming.default_expiration = 60 * 60
  api.chromium_swarming.default_hard_timeout = 60 * 60
  api.chromium_swarming.default_io_timeout = 20 * 60
  api.chromium_swarming.default_idempotent = True
  api.chromium_swarming.default_priority = 30
  api.chromium_swarming.default_user = 'joe'
  api.chromium_swarming.set_default_env('TESTING', '1')
  api.chromium_swarming.verbose = True
  api.chromium_swarming.task_output_stdout = 'json'

  api.chromium_swarming.set_default_dimension('inexistent', None)

  api.chromium_swarming.show_outputs_ref_in_collect_step = (
    show_outputs_ref_in_collect_step
  )

  try:
    # Testing ReadOnlyDict.__setattr__() coverage.
    api.chromium_swarming.default_dimensions['invalid'] = 'foo'
  except TypeError:
    pass
  try:
    api.chromium_swarming.default_env['invalid'] = 'foo'
  except TypeError:
    pass

  # Create a temp dir to put *.isolated files into.
  temp_dir = api.path.mkdtemp('hello_isolated_world')

  # Make sure the created Swarming requests all have `pool` in its dimension
  api.chromium_swarming.set_default_dimension('pool', 'foo')

  # Prepare a bunch of swarming tasks to run hello_world on multiple platforms.
  tasks = []
  resultdb_spec.setdefault('base_variant', {}).update(
    {'builder': api.buildbucket.builder_name}
  )
  resultdb = ResultDB.create(**resultdb_spec)
  for platform in platforms:
    # Isolate example hello_world.isolate from swarming client repo.
    # TODO(vadimsh): Add a thin wrapper around isolate to 'isolate' module?
    step_result = api.step(
      'archive for %s' % platform,
      [
        'tools/luci-go/isolate',
        'archive',
        '-isolate',
        api.swarming_client.path.joinpath(
          'example', 'payload', 'hello_world.isolate'
        ),
        '-verbose',
      ],
      stdout=api.raw_io.output_text(),
    )
    # TODO(vadimsh): Pass result from isolate though --output-json option.
    cas_input_root = step_result.stdout.split()[0].strip()

    # Create a task to run the isolated file on swarming, set OS dimension.
    # Also generate code coverage for multi-shard case by triggering multiple
    # shards on Linux.
    if gtest_task:
      task = api.chromium_swarming.gtest_task(
        raw_cmd=raw_cmd,
        name='hello_world',
        cas_input_root=cas_input_root,
        task_output_dir=temp_dir / 'task_output_dir',
        merge=merge,
      )
    elif isolated_script_task:
      task = api.chromium_swarming.isolated_script_task()
      task_request = task.request
      task_slice = task_request[0]

      task_slice = task_slice.with_cas_input_root(cas_input_root).with_env_vars(
        **{'IS_GTEST': '', 'IS_SCRIPTTEST': 'True'}
      )
      task.request = task_request.with_slice(0, task_slice).with_name(
        'hello_world'
      )

      if realm:
        task.request = task.request.with_realm(realm)

      task.task_output_dir = temp_dir / 'task_output_dir'
      if merge:
        task.merge = merge
      task.trigger_script = trigger_script
    else:
      task = api.chromium_swarming.task(
        name='hello_world',
        cas_input_root=cas_input_root,
        extra_args=['--foo', '42'],
        task_output_dir=temp_dir / 'task_output_dir',
        named_caches=named_caches,
        service_account=service_account,
        cipd_packages=[
          chromium_swarming.CipdPackage.create(
            name='cool/package',
            version='vers',
            root='',
          )
        ],
      )
    assert platform in ('linux', 'mac', 'win')
    if platform == 'linux':
      target_os = 'Ubuntu-16.04'
      task.shards = 2
      task.shard_indices = range(task.shards)
    elif platform == 'mac':
      target_os = 'Mac-10.13'
      task.shards = 3
      task.shard_indices = [1]
    else:
      target_os = 'Windows-10'
      task.shards = 1
      task.shard_indices = [0]
    if custom_trigger_script:
      task.trigger_script = chromium_swarming.TriggerScript.create(
        script=api.path.cache_dir / 'custom_trigger.py'
      )

    task_request = task.request
    task_slice = task_request[0]
    task_dimensions = task_slice.dimensions
    task_dimensions['os'] = target_os
    task.tags.add('os:' + platform)

    # test_suite is required, if resultdb is enabled.
    if resultdb.enable:
      task.tags.add('test_suite:chromium_test')
    ensure_file = task_slice.cipd_ensure_file
    ensure_file.add_package('super/awesome/pkg', 'git_revision:deadbeef', 'bin')
    task_slice = task_slice.with_dimensions(
      **task_dimensions
    ).with_cipd_ensure_file(ensure_file)
    task.request = task_request.with_slice(0, task_slice)
    tasks.append(task)

  # Launch all tasks.
  for task in tasks:
    api.chromium_swarming.trigger_task(task, resultdb=resultdb)
    assert len(task.get_task_shard_output_dirs()) == len(task.shard_indices)

  # Recipe can do something useful here locally while tasks are
  # running on swarming.
  api.step('local step', ['echo', 'running something locally'])

  if wait_for_tasks:
    task_ids = [task.get_task_ids() for task in tasks]

    api.chromium_swarming.wait_for_finished_task_set(task_ids)
    api.chromium_swarming.wait_for_finished_task_set(task_ids)
    return

  # Wait for all tasks to complete.
  for task in tasks:
    step_result = api.chromium_swarming.collect_task(task)

  api.chromium_swarming.report_stats()

  # Cleanup.
  api.file.rmtree('remove temp dir', temp_dir)


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'archive for linux',
      stdout=api.raw_io.output_text('hash_for_linux/size hello_world.isolated'),
    ),
    api.step_data(
      'archive for mac',
      stdout=api.raw_io.output_text('hash_for_mac/size hello_world.isolated'),
    ),
    api.properties(platforms=('win', 'linux', 'mac')),
  )

  yield api.test(
    'custom_trigger_script',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for mac',
      stdout=api.raw_io.output_text('hash_for_mac/size hello_world.isolated'),
    ),
    api.properties(platforms=('mac',), custom_trigger_script=True),
    api.post_process(
      post_process.StepCommandContains,
      '[trigger (custom trigger script)] hello_world on Mac-10.13',
      ['--shard-index', '1'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      '[trigger (custom trigger script)] hello_world on Mac-10.13',
      ['--shards', '3'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'default_trigger_script',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for linux',
      stdout=api.raw_io.output_text('hash/size hello_world.isolated'),
    ),
    api.properties(platforms=('linux',), custom_trigger_script=False),
    api.post_check(
      api.swarming.check_triggered_request,
      '[trigger] hello_world on Ubuntu-16.04',
      lambda check, req: check(req[0].env_vars['GTEST_SHARD_INDEX'] == '0'),
      lambda check, req: check(req[0].env_vars['GTEST_TOTAL_SHARDS'] == '2'),
      lambda check, req: check(req[0].command[-2:] == ['--foo', '42']),
    ),
    api.post_check(
      api.swarming.check_triggered_request,
      '[trigger] hello_world on Ubuntu-16.04 (2)',
      lambda check, req: check(req[0].env_vars['GTEST_SHARD_INDEX'] == '1'),
      lambda check, req: check(req[0].env_vars['GTEST_TOTAL_SHARDS'] == '2'),
      lambda check, req: check(req[0].command[-2:] == ['--foo', '42']),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'wait_for_tasks',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'archive for linux',
      stdout=api.raw_io.output_text('hash_for_linux/size hello_world.isolated'),
    ),
    api.step_data(
      'archive for mac',
      stdout=api.raw_io.output_text('hash_for_mac/size hello_world.isolated'),
    ),
    # This is probably how you'd use the test api; testing what happens if one
    # set of tasks finishes first. This example code doesn't care what is
    # returned, but calling code of this usually does.
    api.chromium_swarming.wait_for_finished_task_set(
      [
        ([['110000', '110100']], 1),
        ([['100000'], ['130000']], 1),
      ]
    ),
    api.properties(platforms=('win', 'linux', 'mac'), wait_for_tasks=True),
    api.post_process(
      post_process.Filter('wait for tasks', 'wait for tasks (2)')
    ),
  )

  for exp in [True, False]:
    yield api.test(
      'basic_luci' + ('_experimental' if exp else ''),
      api.runtime(is_experimental=exp),
      api.step_data(
        'archive for win',
        stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
      ),
      api.step_data(
        'archive for linux',
        stdout=api.raw_io.output_text(
          'hash_for_linux/size hello_world.isolated'
        ),
      ),
      api.step_data(
        'archive for mac',
        stdout=api.raw_io.output_text('hash_for_mac/size hello_world.isolated'),
      ),
      api.properties(platforms=('win', 'linux', 'mac')),
      api.expect_status('INFRA_FAILURE'),
    )

  yield api.test(
    'named_caches',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for mac',
      stdout=api.raw_io.output_text('hash_for_mac/size hello_world.isolated'),
    ),
    api.properties(
      platforms=('mac',), named_caches={'foo': 'cache/foo', 'bar': 'cache/bar'}
    ),
  )

  yield api.test(
    'service_account',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'archive for linux',
      stdout=api.raw_io.output_text('hash_for_linux/size hello_world.isolated'),
    ),
    api.step_data(
      'archive for mac',
      stdout=api.raw_io.output_text('hash_for_mac/size hello_world.isolated'),
    ),
    api.properties(
      platforms=(
        'win',
        'linux',
        'mac',
      ),
      service_account='test@example.com',
    ),
  )

  yield api.test(
    'gerrit_trybot',
    api.buildbucket.try_build(
      project='chromium',
      builder='linux',
      build_number=1,
      git_repo='https://chromium.googlesource.com/chromium/src',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
  )

  yield api.test(
    'show_outputs_ref_in_collect_step',
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.properties(show_outputs_ref_in_collect_step=False),
    api.expect_status('INFRA_FAILURE'),
  )

  data = {
    'shards': [
      {
        '': '',
      }
    ]
  }

  yield api.test(
    'gtest_with_outputs_ref',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Windows-10',
      api.chromium_swarming.canned_summary_output(
        api.test_utils.canned_gtest_output(True)
      ),
    ),
  )

  data = {
    'shards': [
      {
        'duration': 120.0,
        'task_id': '0',
        'state': 'COMPLETED',
      }
    ]
  }

  yield api.test(
    'gtest_with_duration',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Windows-10',
      api.chromium_swarming.summary(
        api.test_utils.canned_gtest_output(True), data
      ),
    ),
  )

  data = api.chromium_swarming.canned_summary_output_raw(shards=4)
  data['shards'][2]['completed_ts'] = '2014-09-25T01:49:23.123'
  data['shards'][3]['completed_ts'] = '2014-09-25T01:48:22.345'

  yield api.test(
    'gtest_with_long_task',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Windows-10',
      api.chromium_swarming.summary(
        api.test_utils.canned_gtest_output(True), data
      ),
    ),
  )

  data = {
    'shards': [
      {
        'abandoned_ts': '2014-09-25T01:41:00.123',
        'bot_id': 'vm30',
        'completed_ts': None,
        'created_ts': '2014-09-25T01:41:00.123',
        'duration': 60,
        'failure': False,
        'id': '148aa78d7aa0100',
        'internal_failure': False,
        'modified_ts': '2014-09-25 01:42:00',
        'name': 'heartbeat-canary-2014-09-25_01:41:55-os=Windows',
        'outputs': [],
        'started_ts': '2014-09-25T01:42:11.123',
        'state': 'EXPIRED',
        'task_id': '0',
        'try_number': None,
        'user': 'unknown',
      }
    ],
  }

  data['shards'][0]['state'] = 'EXPIRED'
  yield api.test(
    'swarming_expired_new',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Windows-10', api.chromium_swarming.summary(None, data)
    ),
  )
  yield api.test(
    'isolated_script_expired_new',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Windows-10',
      api.raw_io.output_dir(
        {'summary.json': api.json.dumps(data).encode('utf-8')}
      ),
    ),
    api.properties(isolated_script_task=True),
  )

  data['shards'][0]['state'] = 'TIMED_OUT'
  yield api.test(
    'swarming_timeout_new',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Windows-10', api.chromium_swarming.summary(None, data)
    ),
  )
  yield api.test(
    'isolated_script_timeout_new',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Windows-10',
      api.raw_io.output_dir(
        {'summary.json': api.json.dumps(data).encode('utf-8')}
      ),
    ),
    api.properties(isolated_script_task=True),
  )

  data['shards'][0]['state'] = 'COMPLETED'
  data['shards'][0]['exit_code'] = '1'
  yield api.test(
    'isolated_script_non_zero_exit_status',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Windows-10',
      api.chromium_swarming.summary(
        api.raw_io.output_dir(
          {'summary.json': api.json.dumps(data).encode('utf-8')}
        ),
        data,
      ),
    ),
    api.properties(isolated_script_task=True),
  )

  data['shards'][0]['state'] = 'TIMED_OUT'
  del data['shards'][0]['exit_code']

  big_output_dir = {'summary.json': api.json.dumps(data).encode('utf-8')}
  for i, shard_data in enumerate(
    api.test_utils.generate_simplified_json_results(range(4), True, True)
  ):
    big_output_dir['%s/output.json' % i] = api.json.dumps(shard_data).encode(
      'utf-8'
    )
  # Will cause unicode decode error if tried to decode.
  big_output_dir['0/binary.png'] = b'\x00\x00\x89'
  big_output_dir['0/invalid.txt'] = b'\x00\x00\x89'
  # Large text file
  big_output_dir['0/big_text.txt'] = b'lots of text\n' * 2000
  yield api.test(
    'isolated_large_outdir',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Windows-10', api.raw_io.output_dir(big_output_dir)
    ),
    api.properties(isolated_script_task=True),
  )

  summary_data = {
    'shards': [
      {
        'state': 'COMPLETED',
        'internal_failure': False,
      }
    ]
  }
  json_results = {
    'interrupted': False,
    'version': 3,
    'path_delimiter': '/',
    'seconds_since_epoch': 0,
    'tests': {},
    'num_failures_by_type': {},
    'links': {'custom_link': 'http://example.com'},
  }
  yield api.test(
    'isolated_script_with_custom_merge',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Windows-10',
      api.chromium_swarming.summary(
        api.raw_io.output_dir(
          {'summary.json': api.json.dumps(summary_data).encode('utf-8')}
        )
        + api.json.output(json_results),
        summary_data,
      ),
    ),
    api.properties(
      isolated_script_task=True,
      merge=chromium_swarming.MergeScript.create(
        script=api.path.cache_dir / 'fake_custom_merge_script.py'
      ),
    ),
  )

  yield api.test(
    'isolated_script_with_custom_trigger_script',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Windows-10',
      api.chromium_swarming.summary(
        api.raw_io.output_dir(
          {'summary.json': api.json.dumps(summary_data).encode('utf-8')}
        )
        + api.json.output(json_results),
        summary_data,
      ),
    ),
    api.properties(
      isolated_script_task=True,
      trigger_script=chromium_swarming.TriggerScript.create(
        script=api.path.cache_dir / 'fake_custom_trigger_script.py',
        args=['foo', 'bar'],
      ),
    ),
    api.post_process(
      post_process.Filter(
        '[trigger (custom trigger script)] hello_world on Windows-10'
      )
    ),
  )

  yield api.test(
    'isolated_script_with_realm',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'archive for linux',
      stdout=api.raw_io.output_text('hash_for_linux/size hello_world.isolated'),
    ),
    api.step_data(
      'archive for mac',
      stdout=api.raw_io.output_text('hash_for_mac/size hello_world.isolated'),
    ),
    api.properties(
      platforms=('win', 'linux', 'mac'),
      realm="test:task_realm",
      isolated_script_task=True,
    ),
  )

  yield api.test(
    'isolated_script_with_realm_and_resultdb',
    api.buildbucket.try_build(
      project='chromium', builder='linux', build_number=1
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.step_data(
      'archive for linux',
      stdout=api.raw_io.output_text('hash_for_linux/size hello_world.isolated'),
    ),
    api.step_data(
      'archive for mac',
      stdout=api.raw_io.output_text('hash_for_mac/size hello_world.isolated'),
    ),
    api.properties(
      platforms=('win', 'linux', 'mac'),
      isolated_script_task=True,
      realm="test:task_realm",
      resultdb_spec={'enable': True},
    ),
  )

  summary_data = {
    'shards': [
      None,
      {
        'task_id': '0',
        'state': 'COMPLETED',
        'internal_failure': False,
      },
    ]
  }
  yield api.test(
    'gtest_with_null_shard',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for linux',
      stdout=api.raw_io.output_text('hash_for_linux/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Ubuntu-16.04',
      api.chromium_swarming.summary(
        api.raw_io.output_dir(
          {'summary.json': api.json.dumps(summary_data).encode('utf-8')}
        )
        + api.test_utils.canned_gtest_output(False),
        summary_data,
      ),
    ),
    api.properties(platforms=('linux',), gtest_task=True),
    api.expect_status('INFRA_FAILURE'),
  )
  yield api.test(
    'isolated_script_with_null_shard',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for linux',
      stdout=api.raw_io.output_text('hash_for_linux/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Ubuntu-16.04',
      api.chromium_swarming.summary(
        api.raw_io.output_dir(
          {'summary.json': api.json.dumps(summary_data).encode('utf-8')}
        ),
        summary_data,
      ),
    ),
    api.properties(platforms=('linux',), isolated_script_task=True),
    api.expect_status('INFRA_FAILURE'),
  )
  yield api.test(
    'coverage_gtest_with_null_shard',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.code_coverage(use_clang_coverage=True),
    api.step_data(
      'archive for linux',
      stdout=api.raw_io.output_text('hash_for_linux/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Ubuntu-16.04',
      api.chromium_swarming.summary(
        api.raw_io.output_dir(
          {'summary.json': api.json.dumps(summary_data).encode('utf-8')}
        )
        + api.test_utils.canned_gtest_output(False),
        summary_data,
      ),
    ),
    api.properties(platforms=('linux',), gtest_task=True),
    api.post_process(post_process.StepFailure, 'hello_world on Ubuntu-16.04'),
    api.post_process(post_process.DropExpectation),
  )

  summary_data_deduped = {
    'shards': [
      {
        'state': 'COMPLETED',
        'internal_failure': False,
        'task_id': '1',
      },
      {
        'state': 'COMPLETED',
        'internal_failure': False,
        'deduped_from': None,
        'task_id': '2',
      },
      {
        'state': 'COMPLETED',
        'internal_failure': False,
        'deduped_from': 'deadbeef',
        'duration': 10,
        'task_id': '3',
      },
    ]
  }

  yield api.test(
    'gtest_with_deduped_shard',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for linux',
      stdout=api.raw_io.output_text('hash_for_linux/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Ubuntu-16.04',
      api.chromium_swarming.summary(
        api.raw_io.output_dir(
          {'summary.json': api.json.dumps(summary_data_deduped).encode('utf-8')}
        )
        + api.test_utils.canned_gtest_output(False),
        summary_data_deduped,
      ),
    ),
    api.properties(platforms=('linux',), gtest_task=True),
  )

  missing_duration_data = api.chromium_swarming.canned_summary_output_raw()
  del missing_duration_data['shards'][0]['duration']
  yield api.test(
    'missing_duration',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for linux',
      stdout=api.raw_io.output_text('hash_for_linux/size hello_world.isolated'),
    ),
    api.step_data(
      'hello_world on Ubuntu-16.04',
      api.chromium_swarming.summary(
        api.test_utils.canned_gtest_output(True), missing_duration_data
      ),
    ),
    api.properties(platforms=('linux',), gtest_task=True),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'with_custom_realm',
    api.properties(
      **{
        '$build/chromium_swarming': {
          'task_realm': 'chromium:foo-realm',
        }
      }
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'bad_summary_output_arg',
    api.properties(
      raw_cmd=[
        'chromium:foo-realm',
        '--test-launcher-summary-output=something',
      ],
      gtest_task=True,
    ),
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for win',
      stdout=api.raw_io.output_text('hash_for_win/size hello_world.isolated'),
    ),
    api.expect_exception('ValueError'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'sanitize_spaces_in_repro_instruction',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.step_data(
      'archive for linux',
      stdout=api.raw_io.output_text('hash_for_linux/size hello_world.isolated'),
    ),
    api.properties(platforms=('linux',), gtest_task=True, raw_cmd=['foo bar']),
    api.post_check(
      post_process.StepTextContains,
      'hello_world on Ubuntu-16.04',
      ['"foo bar"'],
    ),
    api.post_process(post_process.DropExpectation),
  )
