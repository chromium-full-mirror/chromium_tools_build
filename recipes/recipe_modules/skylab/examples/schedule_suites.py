# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_checkout, skylab
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    file,
    json as json_module,
    path,
    properties,
    raw_io,
    step,
    time,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium_checkout: chromium_checkout.API
  file: file.API
  json: json_module.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  skylab: skylab.API
  step: step.API
  time: time.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  json: json_module.TEST_API
  properties: properties.TEST_API
  skylab: skylab.TEST_API
  time: time.TEST_API

import base64
import copy
import datetime
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

LACROS_GTEST_ARGS = '--gtest_filter="VaapiTest.*"'
MAYBE_MISSING_VARS = 'ui\\.gaiaPoolDefault|anotherpackage\\..*'
GPU_GTEST_ARGS = ['--show-stdout', '--browser=cros-chrome', '--passthrough']
GPU_EXTRA_BROWSWER_ARGS = ('--log-level=0 --js-flags=--expose-gc '
                           '--force_high_performance_gpu')
LACROS_GCS_PATH = 'gs://fake_bucket/fake_test'
SHARD_COUNT = 2
TAST_MAX_RUN_SEC = 21600

FIELDTRIAL_TAST_VAR_NAME = 'setup.FieldTrialConfig'
FIELDTRIAL_TAST_VAR_VALUE = 'enable'

GITILES_COMMIT = common_pb2.GitilesCommit(
    host='chromium.googlesource.com',
    project='chromium/src',
    ref='refs/heads/main',
    id='a' * 40,
    position=42,
)


def gen_skylab_rdb(suite):
  sources = common_rdb_pb.Sources(
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
    test_args=None,
    cros_img='eve-release/R88-13545.0.0',
    shards=1,
    use_lkgm=False,
)


def gen_skylab_test(name, **kwargs):
  telemetry_shard_index = kwargs.pop('telemetry_shard_index', None)
  k = copy.deepcopy(SKYLAB_TEST_SPEC_TEMPLATE)
  k.update(**kwargs)
  k['resultdb'] = ResultDB.create(**gen_skylab_rdb(name))
  t = SkylabTestSpec.create(name, **k).get_test(SkylabTest)
  t.lacros_gcs_path = LACROS_GCS_PATH

  t.exe_rel_path = 'out/Release/bin/run_foo_unittest'
  t.build_output_dir = 'out/Release'
  if telemetry_shard_index is not None:
    t.telemetry_shard_index = telemetry_shard_index
  return t


REQUESTS = [
    gen_skylab_test(
        'm88_ctp_with_retry',
        shard_level_retries_on_ctp=1,
        cros_model='baks',
        bucket='a_different_chromium_bucket',
        public_builder='ctp-public-builder',
        public_builder_bucket='public-bucket'),
    gen_skylab_test(
        'm88_gtest_test_args',
        test_args=LACROS_GTEST_ARGS,
        autotest_name='chromium',
    ),
    gen_skylab_test(
        'm88_nearby_dut_pool',
        dut_pool='cross_device_multi_cb',
        autotest_name='tast.nearby-share'),
    gen_skylab_test(
        'telemetry_test_args',
        benchmark='speedometer2',
        story_filter='Speedometer2',
        results_label='12345',
        test_shard_map_filename='per_map.json',
        telemetry_shard_index=0,
        autotest_name='chromium_Telemetry'),
    gen_skylab_test(
        # TODO(b/430180607): Remove no longer useful sharding args.
        'sharded_ctp_req',
        dut_pool='cross_device_multi_cb',
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
        test_args=LACROS_GTEST_ARGS,
        autotest_name='chromium',
        strip_chrome=True,
    ),
]

TFC_REQUESTS = [
    gen_skylab_test(
        'tfc_chrome_all_tests',
        shard_level_retries_on_ctp=1,
        cros_board='brya',
        cros_model='kano',
        cros_test_tags=['group:mainline'],
        cros_test_tags_exclude=['informational'],
        cros_test_names_exclude=['tast.disabled.DisabledTest'],
        cros_test_names_exclude_from_file=['dynamically-disabled-tests.txt'],
        cros_test_max_in_shard=30,
        cros_ctp_suite_name='bvt-tast-cq',
        autotest_name='',
    ),
    gen_skylab_test(
        'tfc_explicit_test_list',
        shard_level_retries_on_ctp=1,
        cros_board='brya',
        cros_model='kano',
        cros_test_names=['tast.run.TestA', 'tast.run.TestB'],
        cros_test_names_from_file=['dynamically-disabled-tests.txt'],
        autotest_name='',
    ),
]

BUILD_VARIANT_REQUESTS = [
    gen_skylab_test(
        'build_variant',
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
        autotest_name='tast.nearby-share'),
    gen_skylab_test(
        'multi_dut_skip_secondary_lacros_paths',
        secondary_cros_board='pixel6',
        secondary_cros_img='',
        should_provision_browser_files=[False],
        autotest_name='tast.nearby-share',
    ),
    gen_skylab_test(
        'multi_dut_partial_skip_secondary_lacros_paths',
        secondary_cros_board='atlas,pixel6,octopus',
        secondary_cros_img='atlas-release/R111-15300.0.0,,octopus-release/R111-15300.0.0',
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
        'm88_ctp_with_retry_lkgm',
        use_lkgm=True,
        cros_img='',
        bucket='chromeos-image-archive',
        public_builder='ctp-public-builder',
        public_builder_bucket='public-bucket'),
]

PUBLIC_LKGM_REQUESTS = [
    gen_skylab_test(
        'm88_ctp_with_retry_lkgm',
        use_lkgm=True,
        cros_img='',
        bucket='chromiumos-image-archive')
]

TFC_ARGS_REQUESTS = [
    gen_skylab_test(
        'tast_runtime_var',
        cros_test_tags=['group:mainline'],
        autotest_name='',
        test_args=[
            f"tast.{FIELDTRIAL_TAST_VAR_NAME}={FIELDTRIAL_TAST_VAR_VALUE}"
        ],
        bucket='chromiumos-image-archive'),
    gen_skylab_test(
        'tast_maybemissingvars',
        cros_test_tags=['group:mainline'],
        autotest_name='',
        test_args=[
            "maybemissingvars=" + MAYBE_MISSING_VARS, 'another_flag=123'
        ],
        bucket='chromiumos-image-archive'),
    gen_skylab_test(
        'tast_other_args',
        cros_test_tags=['group:mainline'],
        autotest_name='',
        test_args=["unknown_flag=XYZ", "unknown_boolean_flag"],
        bucket='chromiumos-image-archive'),
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


def RunSteps(api: DEPS, requests):
  api.chromium_checkout.set_paths(api.path.cleanup_dir, 'fake-repo')

  api.buildbucket.set_output_gitiles_commit(GITILES_COMMIT)
  ctp_build_ids = []
  builds = []
  with api.step.nest('schedule skylab test'):
    for r in requests:
      builds.append(
          api.skylab.schedule_suite(
              r,
              '',
              retry_shards=api.properties.get('retry_shards'),
              runtime_no_retry=api.properties.get('runtime_no_retry') or False,
              runtime_override_tests=api.properties.get(
                  'runtime_override_tests'),
              runtime_excluded_tests=api.properties.get(
                  'runtime_excluded_tests')))
      if cpt_id := r.ctp_build_ids.get(''):
        ctp_build_ids.append(cpt_id)
  for build in builds:
    if not build:
      raise api.step.StepFailure("")
  api.buildbucket.collect_builds(ctp_build_ids, timeout=60)
  with api.step.nest('find test runner build'):
    for r in requests:
      api.skylab.fetch_test_runners(r, '')


def GenTests(api: TEST_DEPS):

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
                           '--ctp-builder-name',
                           REQUESTS[0].spec.public_builder,
                           '--ctp-bucket',
                           REQUESTS[0].spec.public_builder_bucket,
                       ]),
      api.skylab.mock_wait_on_suites('find test runner build', 1),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'reduced timeout',
      api.properties(requests=REQUESTS[:1]),
      api.buildbucket.ci_build(
          build_id=8912345678999999999,
          execution_timeout=3600,  # 1h
          start_time=datetime.datetime(
              2025, 10, 30, 6, 50, 0, tzinfo=datetime.timezone.utc)),
      api.time.seed(
          datetime.datetime(
              2025, 10, 30, 7, 0, 0, tzinfo=datetime.timezone.utc).timestamp()),
      api.time.step(60),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + REQUESTS[0].name + '.schedule',
          [
              '--timeout-mins',
              # builder timeout(60min) - elapsed time(10min) -
              # reserved for all kinds of overhead(5min) - recipe_module/time
              # step(1min)
              '44',
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
          'schedule skylab test.' + REQUESTS[2].name + '.schedule',
          ['--test-arg', 'resultdb_settings',
           re.compile('[^-].*')]),
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
              '--parent-build-id', '0', '--builder-name', '', '--board', 'eve',
              '--ctp-builder-name', 'cros_test_platform', '--ctp-bucket',
              'testplatform', '--pool', 'DUT_POOL_QUOTA', '--image',
              'eve-arc-t-release/R111-15300.0.0', '--timeout-mins', '60',
              '--qs-account', 'chrome'
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
              '--builder-name',
              '',
              '--board',
              'eve',
              '--ctp-builder-name',
              'cros_test_platform',
              '--ctp-bucket',
              'testplatform',
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
      api.properties(requests=TFC_ARGS_REQUESTS),
      api.post_process(
          StepCommandContainsSubstrings,
          'schedule skylab test.' + TFC_ARGS_REQUESTS[0].name + '.schedule', [
              '--test-arg', 'tast.' + FIELDTRIAL_TAST_VAR_NAME,
              FIELDTRIAL_TAST_VAR_VALUE
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'tast_maybe_missing_vars',
      api.properties(requests=TFC_ARGS_REQUESTS),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_ARGS_REQUESTS[1].name + '.schedule',
          ['--test-arg', 'maybemissingvars', MAYBE_MISSING_VARS]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'tast_unknown_arg',
      api.properties(requests=TFC_ARGS_REQUESTS),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_ARGS_REQUESTS[2].name + '.schedule', [
              '--test-arg', 'unknown_flag', 'XYZ', '--test-arg',
              'unknown_boolean_flag', 'True'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'multi_dut',
      api.properties(requests=MULTI_DUT_REQUESTS[0:1]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + MULTI_DUT_REQUESTS[0].name + '.schedule', [
              'request', '--chromium-suite-name', 'multi_dut',
              '--parent-build-id', '0', '--builder-name', '', '--board', 'eve',
              '--ctp-builder-name', 'cros_test_platform', '--ctp-bucket',
              'testplatform', '--pool', 'DUT_POOL_QUOTA', '--image',
              'eve-release/R88-13545.0.0', '--secondary-boards', 'eve',
              '--secondary-images', 'eve-release/R88-13545.0.0',
              '--timeout-mins', '60', '--qs-account', 'chrome'
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
              '--builder-name',
              '',
              '--board',
              'eve',
              '--ctp-builder-name',
              'cros_test_platform',
              '--ctp-bucket',
              'testplatform',
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
          'schedule skylab test.m88_ctp_with_retry_lkgm.call build API', False),
      api.post_process(
          check_find_lkgm_build_target,
          'schedule skylab test.m88_ctp_with_retry_lkgm.call build API', 'eve'),
      api.step_data(
          'schedule skylab test.m88_ctp_with_retry_lkgm.call build API',
          api.json.output({
              "chromeosLkgm": "15581.0.0",
              "configName": "eve-release",
              "fullVersion": "R118-15580.0.0"
          })),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + LKGM_REQUESTS[0].name + '.schedule', [
              'request', '--chromium-suite-name', 'm88_ctp_with_retry_lkgm',
              '--parent-build-id', '0', '--builder-name', '', '--board', 'eve',
              '--bucket', 'chromeos-image-archive', '--ctp-builder-name',
              'ctp-public-builder', '--ctp-bucket', 'public-bucket', '--pool',
              'DUT_POOL_QUOTA', '--image', 'eve-release/R118-15580.0.0',
              '--timeout-mins', '60', '--qs-account', 'chrome'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'lkgm-public',
      api.properties(requests=PUBLIC_LKGM_REQUESTS),
      api.post_process(
          check_use_external_config,
          'schedule skylab test.m88_ctp_with_retry_lkgm.call build API', True),
      api.post_process(
          check_find_lkgm_build_target,
          'schedule skylab test.m88_ctp_with_retry_lkgm.call build API', 'eve'),
      api.step_data(
          'schedule skylab test.m88_ctp_with_retry_lkgm.call build API',
          api.json.output({
              "chromeosLkgm": "15581.0.0",
              "configName": "eve-public",
              "fullVersion": "R118-15580.0.0"
          })),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + PUBLIC_LKGM_REQUESTS[0].name + '.schedule',
          [
              'request', '--chromium-suite-name', 'm88_ctp_with_retry_lkgm',
              '--parent-build-id', '0', '--builder-name', '', '--board', 'eve',
              '--bucket', 'chromiumos-image-archive', '--ctp-builder-name',
              'cros_test_platform', '--ctp-bucket', 'testplatform', '--pool',
              'DUT_POOL_QUOTA', '--image', 'eve-public/R118-15580.0.0',
              '--timeout-mins', '60', '--qs-account', 'chrome'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'lkgm_error',
      api.properties(requests=LKGM_REQUESTS),
      api.step_data(
          'schedule skylab test.m88_ctp_with_retry_lkgm.call build API',
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

  yield api.test(
      'tfc-all',
      api.properties(requests=TFC_REQUESTS[:1]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_REQUESTS[0].name + '.schedule', [
              'request', '--chromium-suite-name', 'tfc_chrome_all_tests',
              '--parent-build-id', '0', '--builder-name', '', '--board', 'brya',
              '--model', 'kano', '--ctp-builder-name', 'cros_test_platform',
              '--ctp-bucket', 'testplatform', '--pool', 'DUT_POOL_QUOTA',
              '--image', 'eve-release/R88-13545.0.0', '--timeout-mins', '60',
              '--qs-account', 'chrome'
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_REQUESTS[0].name + '.schedule', [
              '--cros-test-tags',
              'group:mainline',
              '--cros-test-tags-exclude',
              'informational',
              '--cros-test-names-exclude',
              'tast.disabled.DisabledTest',
              '--cros-test-names-exclude-from-file',
              'dynamically-disabled-tests.txt',
              '--cros-ctp-suite-name',
              'bvt-tast-cq',
              '--cros-test-max-in-shard',
              '30',
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_REQUESTS[0].name + '.schedule', [
              '--ash-chrome-gcs-path',
              f'{LACROS_GCS_PATH}/skylab_runtime_deps.tar.zst',
              '--ash-chrome-build-output-dir', 'out/Release'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'tfc-retry',
      api.properties(
          requests=TFC_REQUESTS[:1],
          runtime_excluded_tests=['tast.already.Passing']),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_REQUESTS[0].name + '.schedule', [
              'request', '--chromium-suite-name', 'tfc_chrome_all_tests',
              '--parent-build-id', '0', '--builder-name', '', '--board', 'brya',
              '--model', 'kano', '--ctp-builder-name', 'cros_test_platform',
              '--ctp-bucket', 'testplatform', '--pool', 'DUT_POOL_QUOTA',
              '--image', 'eve-release/R88-13545.0.0', '--timeout-mins', '60',
              '--qs-account', 'chrome'
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_REQUESTS[0].name + '.schedule', [
              '--cros-test-tags',
              'group:mainline',
              '--cros-test-tags-exclude',
              'informational',
              '--cros-test-names-exclude',
              'tast.disabled.DisabledTest',
              '--cros-test-names-exclude-from-file',
              'dynamically-disabled-tests.txt',
              '--cros-test-names-exclude',
              'tast.already.Passing',
              '--cros-ctp-suite-name',
              'bvt-tast-cq',
              '--cros-test-max-in-shard',
              '30',
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_REQUESTS[0].name + '.schedule', [
              '--ash-chrome-gcs-path',
              f'{LACROS_GCS_PATH}/skylab_runtime_deps.tar.zst',
              '--ash-chrome-build-output-dir', 'out/Release'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'tfc-retry-without-patch',
      api.properties(
          requests=TFC_REQUESTS[:1],
          runtime_no_retry=True,
          runtime_override_tests=['tast.already.Failing']),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_REQUESTS[0].name + '.schedule', [
              'request', '--chromium-suite-name', 'tfc_chrome_all_tests',
              '--parent-build-id', '0', '--builder-name', '', '--board', 'brya',
              '--model', 'kano', '--ctp-builder-name', 'cros_test_platform',
              '--ctp-bucket', 'testplatform', '--pool', 'DUT_POOL_QUOTA',
              '--image', 'eve-release/R88-13545.0.0', '--timeout-mins', '60',
              '--qs-account', 'chrome'
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_REQUESTS[0].name + '.schedule', [
              '--cros-test-names',
              'tast.already.Failing',
              '--cros-ctp-suite-name',
              'bvt-tast-cq',
              '--cros-test-max-in-shard',
              '30',
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_REQUESTS[0].name + '.schedule', [
              '--ash-chrome-gcs-path',
              f'{LACROS_GCS_PATH}/skylab_runtime_deps.tar.zst',
              '--ash-chrome-build-output-dir', 'out/Release'
          ]),
      api.post_process(
          post_process.StepCommandDoesNotContain,
          'schedule skylab test.' + TFC_REQUESTS[0].name + '.schedule', [
              '--retry',
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'tfc-with-list',
      api.properties(requests=TFC_REQUESTS[1:]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_REQUESTS[1].name + '.schedule', [
              'request', '--chromium-suite-name', 'tfc_explicit_test_list',
              '--parent-build-id', '0', '--builder-name', '', '--board', 'brya',
              '--model', 'kano', '--ctp-builder-name', 'cros_test_platform',
              '--ctp-bucket', 'testplatform', '--pool', 'DUT_POOL_QUOTA',
              '--image', 'eve-release/R88-13545.0.0', '--timeout-mins', '60',
              '--qs-account', 'chrome'
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_REQUESTS[1].name + '.schedule', [
              '--cros-test-names',
              'tast.run.TestA',
              '--cros-test-names',
              'tast.run.TestB',
              '--cros-test-names-from-file',
              'dynamically-disabled-tests.txt',
          ]),
      api.post_process(
          post_process.StepCommandContains,
          'schedule skylab test.' + TFC_REQUESTS[1].name + '.schedule', [
              '--ash-chrome-gcs-path',
              f'{LACROS_GCS_PATH}/skylab_runtime_deps.tar.zst',
              '--ash-chrome-build-output-dir', 'out/Release'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'qs-account-fyi',
      api.properties(requests=REQUESTS[:1]),
      api.buildbucket.ci_build(builder='fyi-builder',),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[0].name + '.schedule',
                       ['--qs-account', 'chrome_fyi']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'qs-account-try-no-parent',
      api.properties(requests=REQUESTS[:1]),
      api.buildbucket.try_build(builder='try-builder',),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[0].name + '.schedule',
                       ['--qs-account', 'chrome_cq']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'qs-account-try-with-parent',
      api.properties(requests=REQUESTS[:1]),
      api.buildbucket.try_build(
          builder='try-builder',
          ancestor_ids=[8945511751514863184],
      ),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[0].name + '.schedule',
                       ['--qs-account', 'chrome']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'qs-account-ci-preuprev',
      api.properties(requests=REQUESTS[:1]),
      api.buildbucket.ci_build(builder='chromeos-preuprev-builder',),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[0].name + '.schedule',
                       ['--qs-account', 'chrome_cq']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cros-test-platform-staging-ci-betty',
      api.properties(requests=REQUESTS[1:2]),
      api.buildbucket.ci_build(
          builder='chromeos-betty-chrome-gtest', build_id=8900000000000000018),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[1].name + '.schedule',
                       [
                           '--ctp-builder-name', 'cros_test_platform-staging',
                           '--ctp-bucket', 'testplatform'
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cros-test-platform-staging-ci-volteer',
      api.properties(requests=REQUESTS[1:2]),
      api.buildbucket.ci_build(
          builder='chromeos-volteer-chrome-gtest',
          build_id=8900000000000000018),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[1].name + '.schedule',
                       [
                           '--ctp-builder-name', 'cros_test_platform-staging',
                           '--ctp-bucket', 'testplatform'
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cros-test-platform-staging-ci-other-builder',
      api.properties(requests=REQUESTS[1:2]),
      api.buildbucket.ci_build(
          builder='ci-builder', build_id=8900000000000000018),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'schedule skylab test.' + REQUESTS[1].name + '.schedule',
                       ['--ctp-builder-name', 'cros_test_platform-staging']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cros-test-platform-staging-try-shadow',
      api.properties(requests=REQUESTS[1:2]),
      api.buildbucket.try_build(builder='try-builder', bucket='try.shadow'),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[1].name + '.schedule',
                       [
                           '--ctp-builder-name', 'cros_test_platform-staging',
                           '--ctp-bucket', 'testplatform'
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cros-test-platform-staging-ci-uprev',
      api.properties(requests=REQUESTS[1:2]),
      api.buildbucket.ci_build(
          builder='chromeos-betty-preuprev-builder',
          build_id=8900000000000000018),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'schedule skylab test.' + REQUESTS[1].name + '.schedule',
                       ['--ctp-builder-name', 'cros_test_platform-staging']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cros-test-platform-staging-public-prod-only',
      api.properties(requests=REQUESTS[:1]),
      api.buildbucket.ci_build(
          builder='chromeos-betty-chrome-gtest', build_id=8900000000000000018),
      api.post_process(post_process.StepCommandContains,
                       'schedule skylab test.' + REQUESTS[0].name + '.schedule',
                       [
                           '--ctp-builder-name', 'ctp-public-builder',
                           '--ctp-bucket', 'public-bucket'
                       ]),
      api.post_process(post_process.DropExpectation),
  )
