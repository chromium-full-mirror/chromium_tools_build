# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from PB.recipe_modules.recipe_engine.led.properties import InputProperties

import PB.go.chromium.org.foundry_x.re_client.api.stats.stats as stats_pb

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'reclient',
]

_NINJA_STEP_NAME = 'compile (reclient)'
_BOOTSTRAP_STEP_NAME = 'preprocess for reclient.start reproxy via bootstrap'
_BQ_UPLOAD_DISABLED_STEP_NAME = 'postprocess for reclient.upload RBE metrics to BigQuery (DISABLED)'
_BQ_UPLOAD_STEP_NAME = 'postprocess for reclient.upload RBE metrics to BigQuery'


def RunSteps(api):
  api.path['checkout'] = api.path['tmp_base'].join('checkout')
  ninja_command = ['ninja', '-C', 'out/Release']
  deps_cache_by_step = api.properties.get('deps_cache_by_step', False)
  with api.reclient.process(
      ninja_step_name=_NINJA_STEP_NAME,
      ninja_command=ninja_command,
      deps_cache_by_step=deps_cache_by_step):
    api.step(_NINJA_STEP_NAME, ninja_command)
  _ = api.reclient.instance  # for code coverage
  _ = api.reclient.rewrapper_path
  _ = api.reclient.metrics_project
  _ = api.reclient.jobs


def MakeTestRBEStats(num_records=0, total_verified=None, total_mismatches=None):
  stats = stats_pb.Stats(num_records=num_records)
  if total_verified is not None:
    stats.stats.add(
        name='LocalMetadata.Verification.TotalVerified', count=total_verified)
  if total_mismatches is not None:
    stats.verification.total_mismatches = total_mismatches
  return stats.SerializeToString()


def GenTests(api):
  yield api.test(
      'basic',
      api.reclient.properties(),
  )

  yield api.test(
      'basic windows',
      api.reclient.properties(),
      api.platform('win', 64),
  )

  def no_bq_upload_checker(check, steps):
    check(_BQ_UPLOAD_DISABLED_STEP_NAME in steps)
    check(_BQ_UPLOAD_STEP_NAME not in steps)

  yield api.test(
      'disable_bq_upload',
      api.reclient.properties(disable_bq_upload=True),
      api.post_check(no_bq_upload_checker),
      api.post_process(post_process.DropExpectation),
  )

  def deps_cache_location_checker(check, steps):
    env = steps[_BOOTSTRAP_STEP_NAME].env
    check(_NINJA_STEP_NAME in env['RBE_deps_cache_dir'])

  yield api.test(
      'override deps_cache_by_step',
      api.reclient.properties(),
      api.properties(deps_cache_by_step=True),
      api.post_check(deps_cache_location_checker),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'override instance',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(instance='goma'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'override_metrics_project',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(metrics_project='goma'),
  )

  def metrics_labels_checker(check, steps):
    env = steps["postprocess for reclient.shutdown reproxy via bootstrap"].env
    check("RBE_metrics_labels" in env)
    check(env["RBE_metrics_labels"] ==
          "project=chromium,bucket=ci,builder=Linux reclient,source=led,")

  yield api.test(
      'override_metrics_project_led',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(metrics_project='goma'),
      api.properties(**{
          '$recipe_engine/led': InputProperties(led_run_id='some-led-run'),
      }),
      api.post_check(metrics_labels_checker),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'override_cache_silo',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(cache_silo='goma'),
  )

  yield api.test(
      'override_scandeps_server',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(scandeps_server=True),
  )

  def env_checker(check, steps):
    env = steps[_NINJA_STEP_NAME].env
    check(env['RBE_FOO'] == 'foo')
    check(env['RBE_BAR'] == 'bar')

  yield api.test(
      'proper_rewrapper_flags',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(rewrapper_env={
          'RBE_FOO': 'foo',
          'RBE_BAR': 'bar'
      }),
      api.post_check(env_checker),
  )

  def bootstrap_env_checker(check, steps):
    env = steps[_BOOTSTRAP_STEP_NAME].env
    check(env['RBE_FOO'] == 'foo')
    check(env['RBE_BAR'] == 'bar')

  yield api.test(
      'proper_bootstrap_flags',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(bootstrap_env={
          'RBE_FOO': 'foo',
          'RBE_BAR': 'bar'
      }),
      api.post_check(bootstrap_env_checker),
      api.post_process(post_process.DropExpectation),
  )

  def glog_env_checker(check, steps):
    env = steps[_NINJA_STEP_NAME].env
    check(env['GLOG_vmodule'] == 'abc*=2')
    check(env['GLOG_v'] == '10')

  yield api.test(
      'proper_glog_flags',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(rewrapper_env={
          'GLOG_vmodule': 'abc*=2',
          'GLOG_v': '10',
      }),
      api.post_check(glog_env_checker),
      api.post_process(post_process.DropExpectation),
  )

  def goma_env_checker(check, steps):
    env = steps[_NINJA_STEP_NAME].env
    check(env['GOMA_COMPILER_PROXY_ENABLE_CRASH_DUMP'] == 'true')

  yield api.test(
      'proper_goma_flags',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(rewrapper_env={
          'GOMA_COMPILER_PROXY_ENABLE_CRASH_DUMP': 'true',
      }),
      api.post_check(goma_env_checker),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'incorrect_rewrapper_flags',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(rewrapper_env={
          'MISSING_RBE_PREFIX': 'foo',
      }),
      api.expect_exception('MalformedREClientFlag'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'profiler',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(profiler_service='reclient'),
      api.post_process(
          post_process.Filter(
              'preprocess for reclient.start reproxy via bootstrap')),
  )

  yield api.test(
      'crash dump upload',
      api.reclient.properties(),
      api.override_step_data(
          'postprocess for reclient.list reclient log directory',
          api.file.listdir(['abcd.dmp'])),
      api.post_process(
          post_process.Filter().include_re(r'.*reproxy crash dumps.*')),
  )

  yield api.test(
      'trace',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(publish_trace=True),
      api.post_process(
          post_process.Filter(
              'postprocess for reclient.upload reclient traces')),
  )

  yield api.test(
      'ensure_verified_succeed',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(ensure_verified=True),
      api.step_data(
          'postprocess for reclient.load rbe_metrics.pb',
          api.file.read_raw(
              content=MakeTestRBEStats(num_records=1, total_verified=1))),
      api.post_process(post_process.StepSuccess,
                       'postprocess for reclient.verification'),
      api.post_process(
          post_process.Filter('postprocess for reclient.verification')),
  )

  yield api.test(
      'ensure_verified_no_records',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(ensure_verified=True),
      api.step_data('postprocess for reclient.load rbe_metrics.pb',
                    api.file.read_raw(content=MakeTestRBEStats(num_records=0))),
      api.post_process(post_process.StepSuccess,
                       'postprocess for reclient.verification'),
      api.post_process(
          post_process.Filter('postprocess for reclient.verification')),
  )

  yield api.test(
      'ensure_verified_no_verified_field',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(ensure_verified=True),
      api.step_data('postprocess for reclient.load rbe_metrics.pb',
                    api.file.read_raw(content=MakeTestRBEStats(num_records=1))),
      api.post_process(post_process.StepException,
                       'postprocess for reclient.verification'),
      api.post_process(
          post_process.Filter('postprocess for reclient.verification')),
  )

  yield api.test(
      'ensure_verified_no_verification',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(ensure_verified=True),
      api.step_data(
          'postprocess for reclient.load rbe_metrics.pb',
          api.file.read_raw(
              content=MakeTestRBEStats(num_records=1, total_verified=0))),
      api.post_process(post_process.StepException,
                       'postprocess for reclient.verification'),
      api.post_process(
          post_process.Filter('postprocess for reclient.verification')),
  )

  yield api.test(
      'ensure_verified_mismatches',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(ensure_verified=True),
      api.step_data(
          'postprocess for reclient.load rbe_metrics.pb',
          api.file.read_raw(
              content=MakeTestRBEStats(
                  num_records=1, total_verified=1, total_mismatches=1))),
      api.post_process(post_process.StepException,
                       'postprocess for reclient.verification'),
      api.post_process(
          post_process.Filter('postprocess for reclient.verification')),
  )
