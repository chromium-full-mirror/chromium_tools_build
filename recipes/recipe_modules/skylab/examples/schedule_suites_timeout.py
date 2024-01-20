# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'skylab',
]

import copy

from RECIPE_MODULES.build.chromium_tests.resultdb import ResultDB
from RECIPE_MODULES.build.chromium_tests.steps import SkylabTestSpec, SkylabTest

from recipe_engine import post_process

LACROS_TAST_EXPR = '("group:mainline" && "dep:lacros" && "!informational")'
LACROS_GTEST_ARGS = '--gtest_filter="VaapiTest.*"'
GPU_GTEST_ARGS = ['--show-stdout', '--browser=cros-chrome', '--passthrough']
GPU_EXTRA_BROWSWER_ARGS = ('--log-level=0 --js-flags=--expose-gc '
                           '--force_high_performance_gpu')
LACROS_GCS_PATH = 'gs://fake_bucket/fake_test'
LACROS_SQUASH = 'lacros_compressed.squash'
SHARD_COUNT = 2
TAST_MAX_RUN_SEC = 21600

SKYLAB_TEST_SPEC_TEMPLATE = dict(
    autotest_name='lacros.tast',
    cros_board='eve',
    cros_model='',
    tast_expr=None,
    tast_expr_key='default',
    test_args=None,
    cros_img='eve-release/R88-13545.0.0',
    shards=1,
)


def gen_skylab_test(name, **kwargs):
  k = copy.deepcopy(SKYLAB_TEST_SPEC_TEMPLATE)
  k.update(**kwargs)
  t = SkylabTestSpec.create(name, **k).get_test(SkylabTest)
  t.lacros_gcs_path = LACROS_GCS_PATH
  t.ctp_build_ids[''] = [1234]
  return t


request = gen_skylab_test(
    'm88_tast_without_retry',
    tast_expr=LACROS_TAST_EXPR,
    retries=0,
    cros_model='baks',
    bucket='a_different_chromium_bucket',
    public_builder='ctp-public-builder',
    public_builder_bucket='public-bucket')


def RunSteps(api):
  api.skylab.wait_on_suites([request], '', timeout_seconds=10)


def GenTests(api):

  # If timeout, 'collect skylab results' step should raise an EXCEPTION and
  # not block the following steps.
  yield api.test(
      'collect_results_failure_by_timeout',
      api.step_data(
          'collect skylab results.buildbucket.collect.wait',
          times_out_after=12),
      api.post_process(post_process.StepException, 'collect skylab results'),
      api.post_process(post_process.MustRun, 'find test runner build'),
      api.post_process(post_process.DropExpectation),
  )
