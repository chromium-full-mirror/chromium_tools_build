# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'skylab',
]

import base64
import copy
import json

from RECIPE_MODULES.build.chromium_tests.resultdb import ResultDB
from RECIPE_MODULES.build.chromium_tests.steps import SkylabTestSpec, SkylabTest

from recipe_engine import post_process

LACROS_TAST_EXPR = '("group:mainline" && "dep:lacros" && "!informational")'
LACROS_GTEST_ARGS = '--gtest_filter="VaapiTest.*"'
GPU_GTEST_ARGS = ['--show-stdout', '--browser=cros-chrome', '--passthrough']
GPU_EXTRA_BROWSWER_ARGS = ('--log-level=0 --js-flags=--expose-gc '
                           '--force_high_performance_gpu')
LACROS_GCS_PATH = 'gs://fake_bucket/lacros.squashfs'
SHARD_COUNT = 2
TAST_MAX_RUN_SEC = 21600


def gen_skylab_rdb(suite):
  return {
      'base_variant': {
          'cros_img': 'eve-release/R88-13545.0.0',
          'device_type': 'eve',
          'os': 'ChromeOS',
          'test_suite': suite,
      },
      'coerce_negative_duration': True,
      'enable': True,
      'exonerate_unexpected_pass': True,
      'has_native_resultdb_integration': False,
      'include': False,
      'result_adapter_path': 'result_adapter',
      'result_format': 'tast',
      'test_id_as_test_location': False,
  }


SKYLAB_TEST_SPEC_TEMPLATE = dict(
    autotest_name='lacros.tast',
    cros_board='eve',
    tast_expr=None,
    tast_expr_key='default',
    test_args=None,
    cros_img='eve-release/R88-13545.0.0',
    retries=0,
    shards=1,
)


def gen_skylab_test(name, **kwargs):
  tast_expr_file = kwargs.pop('tast_expr_file', None)
  telemetry_shard_index = kwargs.pop('telemetry_shard_index', None)
  k = copy.deepcopy(SKYLAB_TEST_SPEC_TEMPLATE)
  k.update(**kwargs)
  k['resultdb'] = ResultDB.create(**gen_skylab_rdb(name))
  t = SkylabTestSpec.create(name, **k).get_test(SkylabTest)
  t.lacros_gcs_path = LACROS_GCS_PATH

  if t.is_tast_test:
    t.exe_rel_path = 'out/Release/chrome'
  else:
    t.exe_rel_path = 'out/Release/bin/run_foo_unittest'
  if tast_expr_file:
    t.tast_expr_file = tast_expr_file
  if telemetry_shard_index is not None:
    t.telemetry_shard_index = telemetry_shard_index
  return t


REQUESTS = [
    gen_skylab_test(
        'm88_tast_with_retry',
        tast_expr=LACROS_TAST_EXPR,
        retries=3,
        bucket='a_different_chromium_bucket',
        public_builder='ctp-public-builder',
        public_builder_bucket='public-bucket'),
    gen_skylab_test(
        'm88_gtest_test_args', tast_expr=None, test_args=LACROS_GTEST_ARGS),
    gen_skylab_test(
        'm88_nearby_dut_pool',
        tast_expr=LACROS_TAST_EXPR,
        dut_pool='cross_device_multi_cb',
        tast_expr_file='tast_expr_file.filter'),
    gen_skylab_test(
        'm88_nearby_multi_dut',
        secondary_cros_board='eve',
        secondary_cros_img='eve-release/R88-13545.0.0',
        tast_expr=LACROS_TAST_EXPR,
        autotest_name='tast.nearby-share'),
    gen_skylab_test(
        'telemetry_test_args',
        tast_expr=None,
        benchmark='speedometer2',
        story_filter='Speedometer2',
        results_label='12345',
        test_shard_map_filename='per_map.json',
        telemetry_shard_index=0),
    gen_skylab_test(
        'sharded_tast_req',
        tast_expr=LACROS_TAST_EXPR,
        dut_pool='cross_device_multi_cb',
        tast_expr_file='tast_expr_file.filter',
        max_run_sec=TAST_MAX_RUN_SEC,
        shards=SHARD_COUNT),
    gen_skylab_test(
        'm109_gpu_tests',
        test_args=GPU_GTEST_ARGS,
        autotest_name='chromium_Graphics',
        bucket='chromiumos-image-archive',
        extra_browser_args=GPU_EXTRA_BROWSWER_ARGS,
    ),
    gen_skylab_test(
        'm111_multi_dut_skip_secondary_lacros_paths',
        secondary_cros_board='pixel6',
        should_provision_browser_files=[False],
    ),
    gen_skylab_test(
        'm111_multi_dut_partial_skip_secondary_lacros_paths',
        secondary_cros_board='atlas,pixel6,octopus',
        secondary_cros_img='atlas-release/R111-15300.0.0,,octopus-release/R111-15300.0.0',
        should_provision_browser_files=[True, False, True],
    ),
]


def RunSteps(api):
  build_ids = api.skylab.schedule_suites(REQUESTS)
  api.skylab.wait_on_suites(build_ids, timeout_seconds=3600)

def GenTests(api):

  def b64_encode(s):
    return base64.b64encode(s.encode('utf-8')).decode('ascii')

  def test_args_for_shard(name,
                          shard,
                          shard_count=SHARD_COUNT,
                          max_run_sec=TAST_MAX_RUN_SEC):
    return 'resultdb_settings={} '\
        'tast_expr_b64={} '\
        'exe_rel_path=out/Release/chrome '\
        'tast_expr_file=tast_expr_file.filter '\
        'tast_expr_key=default '\
        'max_run_sec={} '\
        'shard_index={} '\
        'total_shards={}'.format(
            b64_encode(json.dumps(gen_skylab_rdb(name))),
            b64_encode(LACROS_TAST_EXPR), max_run_sec, shard, shard_count)

  yield api.test(
      'basic',
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab tests.' + REQUESTS[0].name + '.schedule', [
              'run', 'test', '-json', '-board', 'eve', '-bucket',
              'a_different_chromium_bucket', '-public-builder',
              'ctp-public-builder', '-public-builder-bucket', 'public-bucket',
              '-pool', 'DUT_POOL_QUOTA', '-image', 'eve-release/R88-13545.0.0',
              '-timeout-mins', '60', '-qs-account', 'lacros', '-max-retries',
              '3'
          ]),
      api.skylab.mock_wait_on_suites('find test runner build', len(REQUESTS)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'fail_request_continues',
      api.step_data(
          'schedule skylab tests.' + REQUESTS[0].name + '.schedule', retcode=1),
      api.post_process(post_process.StepFailure, 'schedule skylab tests'),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab tests.' + REQUESTS[2].name + '.schedule', [
              'run', 'test', '-json', '-board', 'eve', '-pool',
              'cross_device_multi_cb', '-image', 'eve-release/R88-13545.0.0',
              '-timeout-mins', '60', '-qs-account', 'lacros'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multiple_shards_trigger',
      api.step_data(
          'schedule skylab tests.' + REQUESTS[0].name + '.schedule', retcode=1),
      api.post_process(post_process.StepFailure, 'schedule skylab tests'),
      api.post_process(
          post_process.MustRun,
          'schedule skylab tests.{0}.schedule'.format(REQUESTS[5].name)),
      api.post_process(
          post_process.MustRun,
          'schedule skylab tests.{0}.schedule (1)'.format(REQUESTS[5].name)),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab tests.' + REQUESTS[5].name + '.schedule',
          [test_args_for_shard(REQUESTS[5].name, 0)]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab tests.' + REQUESTS[5].name + '.schedule (1)',
          [test_args_for_shard(REQUESTS[5].name, 1)]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'chromium_Graphics_test',
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab tests.' + REQUESTS[6].name + '.schedule',
          'chromium_Graphics'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multi_dut',
      api.step_data(
          'schedule skylab tests.' + REQUESTS[0].name + '.schedule', retcode=1),
      api.post_process(post_process.StepFailure, 'schedule skylab tests'),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab tests.' + REQUESTS[3].name + '.schedule', [
              'run', 'test', '-json', '-board', 'eve', '-secondary-boards',
              'eve', '-secondary-images', 'eve-release/R88-13545.0.0', '-pool',
              'DUT_POOL_QUOTA', '-image', 'eve-release/R88-13545.0.0',
              '-timeout-mins', '60', '-qs-account', 'lacros', '-lacros-path',
              'gs://fake_bucket/lacros.squashfs', '-secondary-lacros-paths',
              'gs://fake_bucket/lacros.squashfs'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multi_dut_skip_provision_browser_files',
      api.step_data(
          'schedule skylab tests.' + REQUESTS[0].name + '.schedule', retcode=1),
      api.post_process(post_process.StepFailure, 'schedule skylab tests'),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab tests.' + REQUESTS[7].name + '.schedule', [
              'run', 'test', '-json', '-board', 'eve', '-secondary-boards',
              'pixel6', '-pool', 'DUT_POOL_QUOTA',
              '-image', 'eve-release/R88-13545.0.0', '-timeout-mins', '60',
              '-qs-account', 'lacros', '-lacros-path',
              'gs://fake_bucket/lacros.squashfs'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multi_dut_partial_skip_provision_browser_files',
      api.step_data(
          'schedule skylab tests.' + REQUESTS[0].name + '.schedule', retcode=1),
      api.post_process(post_process.StepFailure, 'schedule skylab tests'),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab tests.' + REQUESTS[8].name + '.schedule', [
              'run', 'test', '-json', '-board', 'eve', '-secondary-boards',
              'atlas,pixel6,octopus', '-secondary-images',
              'atlas-release/R111-15300.0.0,,octopus-release/R111-15300.0.0',
              '-pool', 'DUT_POOL_QUOTA', '-image', 'eve-release/R88-13545.0.0',
              '-timeout-mins', '60', '-qs-account', 'lacros', '-lacros-path',
              'gs://fake_bucket/lacros.squashfs', '-secondary-lacros-paths',
              'gs://fake_bucket/lacros.squashfs,,gs://fake_bucket/lacros.squashfs'
          ]),
      api.post_process(post_process.DropExpectation),
  )
