# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium_checkout',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'skylab',
]

import base64
import copy
import json
import re

from google.protobuf import json_format

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import common as common_rdb_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import (invocation as
                                                       invocation_pb)

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from RECIPE_MODULES.build.chromium_tests.resultdb import ResultDB
from RECIPE_MODULES.build.chromium_tests.steps import SkylabTestSpec, SkylabTest

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

LACROS_TAST_EXPR = '("group:mainline" && "dep:lacros" && "!informational")'
LACROS_GTEST_ARGS = '--gtest_filter="VaapiTest.*"'
MAYBE_MISSING_VARS = 'ui\\.gaiaPoolDefault|anotherpackage\\..*'
GPU_GTEST_ARGS = ['--show-stdout', '--browser=cros-chrome', '--passthrough']
GPU_EXTRA_BROWSWER_ARGS = ('--log-level=0 --js-flags=--expose-gc '
                           '--force_high_performance_gpu')
LACROS_GCS_PATH = 'gs://fake_bucket/fake_test'
SHARD_COUNT = 2
TAST_MAX_RUN_SEC = 21600

FIELDTRIAL_TAST_VAR = 'setup.FieldTrialConfig=enable'

GITILES_COMMIT = common_pb2.GitilesCommit(
    host='chromium.googlesource.com',
    project='chromium/src',
    ref='refs/heads/main',
    id='a' * 40,
    position=42,
)


def gen_skylab_rdb(suite):
  sources = invocation_pb.Sources(
      gitiles_commit=common_rdb_pb.GitilesCommit(
          host=GITILES_COMMIT.host,
          project=GITILES_COMMIT.project,
          commit_hash=GITILES_COMMIT.id,
          ref=GITILES_COMMIT.ref,
          position=GITILES_COMMIT.position,
      ))
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
      'sources': json_format.MessageToJson(sources),
      'test_id_as_test_location': False,
  }


SKYLAB_TEST_SPEC_TEMPLATE = dict(
    autotest_name='tast.lacros',
    cros_board='eve',
    cros_model='',
    tast_expr=None,
    tast_expr_key='default',
    test_args=None,
    cros_img='eve-release/R88-13545.0.0',
    retries=0,
    shards=1,
    use_lkgm=False,
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
        shard_level_retries_on_ctp=1,
        cros_model='baks',
        bucket='a_different_chromium_bucket',
        public_builder='ctp-public-builder',
        public_builder_bucket='public-bucket'),
    gen_skylab_test(
        'm88_gtest_test_args',
        tast_expr=None,
        test_args=LACROS_GTEST_ARGS,
        autotest_name='chromium',
    ),
    gen_skylab_test(
        'm88_nearby_dut_pool',
        tast_expr=LACROS_TAST_EXPR,
        dut_pool='cross_device_multi_cb',
        tast_expr_file='tast_expr_file.filter',
        autotest_name='tast.nearby-share'),
    gen_skylab_test(
        'telemetry_test_args',
        tast_expr=None,
        benchmark='speedometer2',
        story_filter='Speedometer2',
        results_label='12345',
        test_shard_map_filename='per_map.json',
        telemetry_shard_index=0,
        autotest_name='chromium_Telemetry'),
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
        'm88_gtest_test_args_strip',
        tast_expr=None,
        test_args=LACROS_GTEST_ARGS,
        autotest_name='chromium',
        strip_chrome=True,
    ),
]

BUILD_VARIANT_REQUESTS = [
    gen_skylab_test(
        'build_variant',
        tast_expr=None,
        test_args=LACROS_GTEST_ARGS,
        cros_board='eve',
        cros_build_target='eve-arc-t',
        cros_img='',
        use_lkgm=True,
        autotest_name='chromium',
    ),
    gen_skylab_test(
        'multi_dut_build_variant',
        secondary_cros_board='atlas,pixel6,octopus',
        secondary_cros_img='use_lkgm,,use_lkgm',
        secondary_cros_build_target='atlas-arc-t,,octopus-arc-t',
        autotest_name='tast.nearby-share',
    ),
]

MULTI_DUT_REQUESTS = [
    gen_skylab_test(
        'multi_dut',
        secondary_cros_board='eve',
        secondary_cros_img='eve-release/R88-13545.0.0',
        tast_expr=LACROS_TAST_EXPR,
        autotest_name='tast.nearby-share'),
    gen_skylab_test(
        'multi_dut_skip_secondary_lacros_paths',
        secondary_cros_board='pixel6',
        secondary_cros_img='',
        tast_expr=LACROS_TAST_EXPR,
        should_provision_browser_files=[False],
        autotest_name='tast.nearby-share',
    ),
    gen_skylab_test(
        'multi_dut_partial_skip_secondary_lacros_paths',
        secondary_cros_board='atlas,pixel6,octopus',
        secondary_cros_img='atlas-release/R111-15300.0.0,,octopus-release/R111-15300.0.0',
        tast_expr=LACROS_TAST_EXPR,
        should_provision_browser_files=[True, False, True],
        autotest_name='tast.nearby-share',
    ),
    gen_skylab_test(
        'multi_dut_secondary_cros_img_use_lkgm',
        secondary_cros_board='atlas,pixel6,octopus',
        secondary_cros_img='use_lkgm,,use_lkgm',
        autotest_name='tast.nearby-share',
    ),
]


LKGM_REQUESTS = [
    gen_skylab_test(
        'm88_tast_with_retry_lkgm',
        tast_expr=LACROS_TAST_EXPR,
        use_lkgm=True,
        cros_img='',
        retries=3,
        bucket='chromeos-image-archive',
        public_builder='ctp-public-builder',
        public_builder_bucket='public-bucket'),
]

PUBLIC_LKGM_REQUESTS = [
    gen_skylab_test(
        'm88_tast_with_retry_lkgm',
        tast_expr=LACROS_TAST_EXPR,
        use_lkgm=True,
        cros_img='',
        retries=3,
        bucket='chromiumos-image-archive')
]

TEST_ARGS_REQUESTS = [
    gen_skylab_test(
        'tast_runtime_var',
        test_args=["tast." + FIELDTRIAL_TAST_VAR],
        bucket='chromiumos-image-archive',
        tast_expr="placeholder: this is tast test"),
    gen_skylab_test(
        'tast_maybemissingvars',
        test_args=[
            "maybemissingvars=" + MAYBE_MISSING_VARS, 'another_flag=123'
        ],
        bucket='chromiumos-image-archive',
        tast_expr="placeholder: this is tast test"),
    gen_skylab_test(
        'tast_other_args',
        test_args=["unknown_flag=XYZ", "unknown_boolean_flag"],
        bucket='chromiumos-image-archive',
        tast_expr="placeholder: this is tast test"),
]

PROPERTIES = {
    'requests': Property(help="Set of requests", default=[]),
}


def StepCommandContainsSubstrings(check, step_odict, step, substrings):
  """Assert that a step's command contained the given substring

  Args:
    step (str) - The name of the step to check the command of.
    substring (str) - The expected substring of an argument. If any of
      the commandline element contained this substring, the check is success.
  """

  def found_in_commandline(substring):
    return any(substring in arg for arg in step_odict[step].cmd)

  check(
      'command line for step %s contained %s as substrings' %
      (step, substrings), all(found_in_commandline(s) for s in substrings))


def RunSteps(api, requests):
  api.chromium_checkout.set_paths(api.path.cleanup_dir, 'fake-repo')

  api.buildbucket.set_output_gitiles_commit(GITILES_COMMIT)
  ctp_build_ids = []
  step_results = []
  with api.step.nest('schedule skylab test'):
    for r in requests:
      step_results.append(
          api.skylab.schedule_suite(
              r, '', retry_shards=api.properties.get('retry_shards')))
      if cpt_id := r.ctp_build_ids.get(''):
        ctp_build_ids.append(cpt_id)
  for result in step_results:
    api.step.raise_on_failure(result)
  api.buildbucket.collect_builds(ctp_build_ids, timeout=60)
  with api.step.nest('find test runner build'):
    for r in requests:
      api.skylab.fetch_test_runners(r, '')


def GenTests(api):

  def check_find_lkgm_build_target(check: post_process.Filter, step_odict: dict,
                                   step: str, value: str):
    """Check that a step's command contained expected build_target value

    Examine the --input-json flag value and check if `build_target.name` value
    is set as expected.

    Args:
    * check: Passed by the recipe test framework.
    * step_odict: Passed by the recipe test framework.
    * step: The name of the step to check the command of.
    * value: The expected value of `build_target.name`
    """

    INPUT_JSON_FLAG_NAME = '--input-json'
    step_cmd = step_odict[step].cmd
    flag_name_position = None
    for i in range(len(step_cmd)):
      if step_cmd[i] == INPUT_JSON_FLAG_NAME:
        flag_name_position = i
        break
    check(f'command line for step {step} contained {INPUT_JSON_FLAG_NAME}',
          flag_name_position is not None)
    input_json = json.loads(step_cmd[flag_name_position + 1])
    check(f'input JSON for step {step} has build_target.name={value}',
          input_json['build_target']['name'] == value)

  def check_use_external_config(check: post_process.Filter, step_odict: dict,
                                step: str, value: bool):
    """Check that a step's command contained expected use_external_config value

    Examine the --input-json flag value and check if `use_external_config` value
    is set as expected.

    Args:
    * check: Passed by the recipe test framework.
    * step_odict: Passed by the recipe test framework.
    * step: The name of the step to check the command of.
    * value: The expected value of `use_external_config`.
    """

    INPUT_JSON_FLAG_NAME = '--input-json'
    step_cmd = step_odict[step].cmd
    flag_name_position = None
    for i in range(len(step_cmd)):
      if step_cmd[i] == INPUT_JSON_FLAG_NAME:
        flag_name_position = i
        break
    check(f'command line for step {step} contained {INPUT_JSON_FLAG_NAME}',
          flag_name_position is not None)
    input_json = json.loads(step_cmd[flag_name_position + 1])
    check(f'input JSON for step {step} has use_external_config={value}',
          input_json['use_external_config'] == value)

  def b64_encode(s):
    return base64.b64encode(s.encode('utf-8')).decode('ascii')

  yield api.test(
      'basic',
      api.properties(requests=REQUESTS[:1]),
      api.buildbucket.ci_build(build_id=8912345678999999999),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[0].name + '.schedule',
                       [
                           '--board',
                           REQUESTS[0].spec.cros_board,
                           '--model',
                           REQUESTS[0].spec.cros_model,
                           '--bucket',
                           REQUESTS[0].spec.bucket,
                           '--public-builder',
                           REQUESTS[0].spec.public_builder,
                           '--public-builder-bucket',
                           REQUESTS[0].spec.public_builder_bucket,
                       ]),
      api.skylab.mock_wait_on_suites('find test runner build', 1),
      api.post_process(post_process.DropExpectation),
  )

  # This test should fail. Because the test did not run.
  yield api.test(
      'fail_request_continues',
      api.properties(requests=REQUESTS),
      api.step_data(
          'schedule skylab test.' + REQUESTS[0].name + '.schedule', retcode=1),
      api.post_process(post_process.StepFailure, 'schedule skylab test'),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[2].name + '.schedule',
                       ['--pool', REQUESTS[2].spec.dut_pool]),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[2].name + '.schedule',
                       ['--autotest-name', REQUESTS[2].spec.autotest_name]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + REQUESTS[2].name + '.schedule', [
              '--test-args',
              re.compile('resultdb_settings=.* '
                         f'tast_expr_file={REQUESTS[2].tast_expr_file} .*'),
          ]),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multiple_shards_trigger',
      api.properties(requests=REQUESTS),
      api.step_data(
          'schedule skylab test.' + REQUESTS[0].name + '.schedule', retcode=1),
      api.post_process(post_process.StepFailure, 'schedule skylab test'),
      api.post_process(
          post_process.MustRun,
          'schedule skylab test.{0}.schedule'.format(REQUESTS[4].name)),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[4].name + '.schedule',
                       ['--total-shards', '2']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'chromium_Graphics_test',
      api.properties(requests=[REQUESTS[5]]),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[5].name + '.schedule',
                       'chromium_Graphics'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'build_variant',
      api.properties(requests=BUILD_VARIANT_REQUESTS),
      # Single DUT
      api.post_process(
          check_find_lkgm_build_target, 'schedule skylab test.' +
          BUILD_VARIANT_REQUESTS[0].name + '.call build API', 'eve-arc-t'),
      api.step_data(
          'schedule skylab test.' + BUILD_VARIANT_REQUESTS[0].name +
          '.call build API',
          api.json.output({
              "chromeosLkgm": "15300.0.0",
              "configName": "eve-arc-t-release",
              "fullVersion": "R111-15300.0.0"
          })),
      api.post_process(
          post_process.StepCommandContains, 'schedule skylab test.' +
          BUILD_VARIANT_REQUESTS[0].name + '.schedule', [
              'request', '--chromium-suite-name', 'build_variant',
              '--parent-build-id', '0', '--board', 'eve', '--pool',
              'DUT_POOL_QUOTA', '--image', 'eve-arc-t-release/R111-15300.0.0',
              '--timeout-mins', '60', '--qs-account', 'lacros'
          ]),
      # Multi-DUT
      api.post_process(
          check_find_lkgm_build_target, 'schedule skylab test.' +
          BUILD_VARIANT_REQUESTS[1].name + '.call build API', 'atlas-arc-t'),
      api.step_data(
          'schedule skylab test.' + BUILD_VARIANT_REQUESTS[1].name +
          '.call build API',
          api.json.output({
              "chromeosLkgm": "15580.0.0",
              "configName": "atlas-arc-t-release",
              "fullVersion": "R118-15580.0.0"
          })),
      api.post_process(
          check_find_lkgm_build_target, 'schedule skylab test.' +
          BUILD_VARIANT_REQUESTS[1].name + '.call build API (2)',
          'octopus-arc-t'),
      api.step_data(
          'schedule skylab test.' + BUILD_VARIANT_REQUESTS[1].name +
          '.call build API (2)',
          api.json.output({
              "chromeosLkgm": "15580.0.0",
              "configName": "octopus-arc-t-release",
              "fullVersion": "R118-15580.0.0"
          })),
      api.post_process(
          post_process.StepCommandContains, 'schedule skylab test.' +
          BUILD_VARIANT_REQUESTS[1].name + '.schedule', [
              'request',
              '--chromium-suite-name',
              'multi_dut_build_variant',
              '--parent-build-id',
              '0',
              '--board',
              'eve',
              '--pool',
              'DUT_POOL_QUOTA',
              '--image',
              'eve-release/R88-13545.0.0',
              '--secondary-boards',
              'atlas',
              '--secondary-images',
              'atlas-arc-t-release/R118-15580.0.0',
              '--secondary-boards',
              'pixel6',
              '--secondary-images',
              '',
              '--secondary-boards',
              'octopus',
              '--secondary-images',
              'octopus-arc-t-release/R118-15580.0.0',
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'tast_vars',
      api.properties(requests=TEST_ARGS_REQUESTS),
      api.post_process(
          StepCommandContainsSubstrings,
          'schedule skylab test.' + TEST_ARGS_REQUESTS[0].name + '.schedule',
          ['tast.' + FIELDTRIAL_TAST_VAR]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'tast_maybe_missing_vars',
      api.properties(requests=TEST_ARGS_REQUESTS),
      api.post_process(
          StepCommandContainsSubstrings,
          'schedule skylab test.' + TEST_ARGS_REQUESTS[1].name + '.schedule',
          ['maybemissingvars_b64=' + b64_encode(MAYBE_MISSING_VARS)]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'tast_unknown_arg',
      api.properties(requests=TEST_ARGS_REQUESTS),
      api.post_process(
          StepCommandContainsSubstrings,
          'schedule skylab test.' + TEST_ARGS_REQUESTS[2].name + '.schedule',
          ['unknown_flag=XY', 'unknown_boolean_flag']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multi_dut',
      api.properties(requests=MULTI_DUT_REQUESTS[0:1]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + MULTI_DUT_REQUESTS[0].name + '.schedule', [
              'request', '--chromium-suite-name', 'multi_dut',
              '--parent-build-id', '0', '--board', 'eve', '--pool',
              'DUT_POOL_QUOTA', '--image', 'eve-release/R88-13545.0.0',
              '--secondary-boards', 'eve', '--secondary-images',
              'eve-release/R88-13545.0.0', '--timeout-mins', '60',
              '--qs-account', 'lacros'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multi_dut_skip_provision_browser_files',
      api.properties(requests=MULTI_DUT_REQUESTS[1:2]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + MULTI_DUT_REQUESTS[1].name + '.schedule', [
              '--secondary-boards',
              MULTI_DUT_REQUESTS[1].spec.secondary_cros_board,
              '--secondary-images',
              MULTI_DUT_REQUESTS[1].spec.secondary_cros_img,
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + MULTI_DUT_REQUESTS[1].name + '.schedule',
          [
              # `should_provision_browser_files=[False]` should map to a single
              # --secondary-lacros-gcs-path arg with an empty string val.
              '--secondary-lacros-gcs-path',
              '',
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multi_dut_partial_skip_provision_browser_files',
      api.properties(requests=MULTI_DUT_REQUESTS[2:3]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + MULTI_DUT_REQUESTS[2].name + '.schedule', [
              '--secondary-boards',
              MULTI_DUT_REQUESTS[2].spec.secondary_cros_board.split(',')[0],
              '--secondary-images',
              MULTI_DUT_REQUESTS[2].spec.secondary_cros_img.split(',')[0],
              '--secondary-boards',
              MULTI_DUT_REQUESTS[2].spec.secondary_cros_board.split(',')[1],
              '--secondary-images',
              MULTI_DUT_REQUESTS[2].spec.secondary_cros_img.split(',')[1],
              '--secondary-boards',
              MULTI_DUT_REQUESTS[2].spec.secondary_cros_board.split(',')[2],
              '--secondary-images',
              MULTI_DUT_REQUESTS[2].spec.secondary_cros_img.split(',')[2],
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multi_dut_secondary_cros_img_use_lkgm',
      api.properties(requests=MULTI_DUT_REQUESTS[3:4]),
      api.post_process(
          check_find_lkgm_build_target, 'schedule skylab test.' +
          MULTI_DUT_REQUESTS[3].name + '.call build API', 'atlas'),
      api.step_data(
          'schedule skylab test.' + MULTI_DUT_REQUESTS[3].name +
          '.call build API',
          api.json.output({
              "chromeosLkgm": "15580.0.0",
              "configName": "atlas-release",
              "fullVersion": "R118-15580.0.0"
          })),
      api.post_process(
          check_find_lkgm_build_target, 'schedule skylab test.' +
          MULTI_DUT_REQUESTS[3].name + '.call build API (2)', 'octopus'),
      api.step_data(
          'schedule skylab test.' + MULTI_DUT_REQUESTS[3].name +
          '.call build API (2)',
          api.json.output({
              "chromeosLkgm": "15580.0.0",
              "configName": "octopus-release",
              "fullVersion": "R118-15580.0.0"
          })),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + MULTI_DUT_REQUESTS[3].name + '.schedule', [
              'request',
              '--chromium-suite-name',
              'multi_dut_secondary_cros_img_use_lkgm',
              '--parent-build-id',
              '0',
              '--board',
              'eve',
              '--pool',
              'DUT_POOL_QUOTA',
              '--image',
              'eve-release/R88-13545.0.0',
              '--secondary-boards',
              'atlas',
              '--secondary-images',
              'atlas-release/R118-15580.0.0',
              '--secondary-boards',
              'pixel6',
              '--secondary-images',
              '',
              '--secondary-boards',
              'octopus',
              '--secondary-images',
              'octopus-release/R118-15580.0.0',
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'lkgm',
      api.properties(requests=LKGM_REQUESTS),
      api.post_process(
          check_use_external_config,
          'schedule skylab test.m88_tast_with_retry_lkgm.call build API',
          False),
      api.post_process(
          check_find_lkgm_build_target,
          'schedule skylab test.m88_tast_with_retry_lkgm.call build API',
          'eve'),
      api.step_data(
          'schedule skylab test.m88_tast_with_retry_lkgm.call build API',
          api.json.output({
              "chromeosLkgm": "15581.0.0",
              "configName": "eve-release",
              "fullVersion": "R118-15580.0.0"
          })),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + LKGM_REQUESTS[0].name + '.schedule', [
              'request', '--chromium-suite-name', 'm88_tast_with_retry_lkgm',
              '--parent-build-id', '0', '--board', 'eve', '--bucket',
              'chromeos-image-archive', '--public-builder',
              'ctp-public-builder', '--public-builder-bucket', 'public-bucket',
              '--pool', 'DUT_POOL_QUOTA', '--image',
              'eve-release/R118-15580.0.0', '--timeout-mins', '60',
              '--qs-account', 'lacros'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'lkgm-public',
      api.properties(requests=PUBLIC_LKGM_REQUESTS),
      api.post_process(
          check_use_external_config,
          'schedule skylab test.m88_tast_with_retry_lkgm.call build API', True),
      api.post_process(
          check_find_lkgm_build_target,
          'schedule skylab test.m88_tast_with_retry_lkgm.call build API',
          'eve'),
      api.step_data(
          'schedule skylab test.m88_tast_with_retry_lkgm.call build API',
          api.json.output({
              "chromeosLkgm": "15581.0.0",
              "configName": "eve-public",
              "fullVersion": "R118-15580.0.0"
          })),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + PUBLIC_LKGM_REQUESTS[0].name + '.schedule',
          [
              'request', '--chromium-suite-name', 'm88_tast_with_retry_lkgm',
              '--parent-build-id', '0', '--board', 'eve', '--bucket',
              'chromiumos-image-archive', '--pool', 'DUT_POOL_QUOTA', '--image',
              'eve-public/R118-15580.0.0', '--timeout-mins', '60',
              '--qs-account', 'lacros'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'lkgm_error',
      api.properties(requests=LKGM_REQUESTS),
      api.step_data(
          'schedule skylab test.m88_tast_with_retry_lkgm.call build API',
          api.json.output({"error": "error occurred, unable to find version"})),
      api.post_process(post_process.StepFailure, 'schedule skylab test'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'retry shards',
      api.properties(requests=REQUESTS[:1], retry_shards=[2]),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[0].name + '.schedule',
                       ['--shard-indexes', '2']),
      api.skylab.mock_wait_on_suites('find test runner build', 1),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'strip-chrome',
      api.properties(requests=REQUESTS[6:7]),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[6].name + '.schedule',
                       '--strip'),
      api.post_process(post_process.DropExpectation),
  )
