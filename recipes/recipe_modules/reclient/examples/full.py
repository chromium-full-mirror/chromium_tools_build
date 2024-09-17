# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from PB.recipe_modules.recipe_engine.led.properties import InputProperties

import PB.go.chromium.org.foundry_x.re_client.api.log.log as log_pb
import PB.go.chromium.org.foundry_x.re_client.api.stats.stats as stats_pb

DEPS = [
    'depot_tools/gclient',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'reclient',
    'siso',
]

_NINJA_STEP_NAME = 'compile (reclient)'
_BOOTSTRAP_STEP_NAME = 'preprocess for reclient.start reproxy via bootstrap'
_BQ_UPLOAD_DISABLED_STEP_NAME = 'postprocess for reclient.upload RBE metrics to BigQuery (DISABLED)'
_BQ_UPLOAD_STEP_NAME = 'postprocess for reclient.upload RBE metrics to BigQuery'
_BOOTSTRAP_EXTRA_ENV = {
    "RBE_bq_table": "reproxylogs.reproxy_log_kokoro",
    "RBE_bq_project": "foundry-x-experiments"
}


def RunSteps(api):
  if 'enable_deps_hook' in api.properties:
    src_cfg = api.gclient.make_config(CACHE_DIR=api.path.cache_dir / 'git')
    soln = src_cfg.solutions.add()
    soln.name = 'src'
    soln.url = 'https://chromium.googlesource.com/chromium/src.git'
    soln.custom_vars = {}
    api.reclient.use_download_remoteexec_cfg_hook(soln)
    api.assertions.assertEqual(soln.custom_vars["download_remoteexec_cfg"],
                               'True')
    api.assertions.assertEqual(
        soln.custom_vars["rbe_instance"],
        'projects/test-rbe-project/instances/default_instance')

  source_dir = api.path.tmp_base_dir / 'checkout'

  # Verify that cache silo can be overridden.
  tmp = api.reclient.cache_silo
  api.reclient.cache_silo = "foobar"
  api.assertions.assertEqual(api.reclient.cache_silo, "foobar")
  api.reclient.cache_silo = tmp

  api.reclient.credentials_helper = 'luci-auth'
  api.reclient.credentials_helper_args = ' '.join([
      'token',
      '-scopes-context',
      '-json-output=-',
      '-json-format=reclient',
      '-lifetime=5m',
  ])

  ninja_command = ['ninja', '-C', 'out/Release']
  deps_cache_by_step = api.properties.get('deps_cache_by_step', False)
  exec_strategy = api.properties.get('exec_strategy', None)
  with api.reclient.process(
      ninja_step_name=_NINJA_STEP_NAME,
      ninja_command=ninja_command,
      source_dir=source_dir,
      deps_cache_by_step=deps_cache_by_step,
      exec_strategy=exec_strategy,
      bootstrap_extra_env=_BOOTSTRAP_EXTRA_ENV,
  ):
    api.step(_NINJA_STEP_NAME, ninja_command)
  _ = api.reclient.instance  # for code coverage
  _ = api.reclient.metrics_project
  _ = api.reclient.jobs
  _ = api.reclient.credentials_helper
  _ = api.reclient.credentials_helper_args


def MakeTestRBEStats(num_records=0,
                     total_verified=None,
                     total_mismatches=None,
                     total_ignored_mismatches=None,
                     mismatches=None):
  stats = stats_pb.Stats(num_records=num_records)
  if total_verified is not None:
    stats.stats.add(
        name='LocalMetadata.Verification.TotalVerified', count=total_verified)
  if total_mismatches is not None:
    stats.verification.total_mismatches = total_mismatches
  if total_ignored_mismatches is not None:
    stats.stats.add(
        name='LocalMetadata.Verification.TotalIgnoredMismatches',
        count=total_ignored_mismatches)
  if mismatches is not None:
    stats.verification.mismatches.extend(mismatches)
  return stats.SerializeToString()


def GenTests(api):
  yield api.test(
      'basic',
      api.reclient.properties(),
  )

  def no_install_reclient_cfgs_checker(check, steps):
    check(
        "preprocess for reclient.install reclient_cfgs (already run by DEPS hook)"
        in steps)
    check("preprocess for reclient.install reclient_cfgs" not in steps)

  yield api.test(
      'install_reclient_cfgs_diabled_by_deps_hook',
      api.properties(
          enable_deps_hook=True,
          gclient_rbe_instance_expected='projects/test-rbe-project/instances/default_instance',
          gclient_download_remoteexec_cfg_expected='True',
      ),
      api.reclient.properties(),
      api.post_check(no_install_reclient_cfgs_checker),
      api.post_process(post_process.DropExpectation),
  )

  def install_reclient_cfgs_checker(check, steps):
    check(
        "preprocess for reclient.install reclient_cfgs (already run by DEPS hook)"
        not in steps)
    check("preprocess for reclient.install reclient_cfgs" in steps)

  yield api.test(
      'install_reclient_cfgs_not_diabled_by_deps_hook_if_missing',
      api.properties(enable_deps_hook=True),
      api.reclient.properties(),
      api.step_data(
          'preprocess for reclient.gclient check if download_remoteexec_cfg var exists',
          retcode=1),
      api.step_data(
          'preprocess for reclient.gclient check if rbe_instance var exists',
          retcode=1),
      api.post_check(install_reclient_cfgs_checker),
      api.post_process(post_process.DropExpectation),
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

  def get_metrics_labels_checker(source='prod', tool='ninja'):

    def checker(check, steps):
      env = steps["postprocess for reclient.shutdown reproxy via bootstrap"].env
      check("RBE_metrics_labels" in env)
      check(
          env["RBE_metrics_labels"] ==
          "project=chromium,bucket=ci,builder=Linux reclient,source=%s,tool=%s"
          % (source, tool))

    return checker

  yield api.test(
      'override_metrics_project',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(metrics_project='goma'),
      api.post_check(get_metrics_labels_checker()),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'override_metrics_project_led',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(metrics_project='goma'),
      api.properties(**{
          '$recipe_engine/led': InputProperties(led_run_id='some-led-run'),
      }),
      api.post_check(get_metrics_labels_checker(source='led')),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'override_metrics_tool_siso',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(metrics_project='goma'),
      api.siso.properties(),
      api.post_check(get_metrics_labels_checker(tool='siso')),
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

  def exec_strategy_env_checker(check, steps):
    env = steps[_NINJA_STEP_NAME].env
    check(env['RBE_exec_strategy'] == 'remote')

  yield api.test(
      'exec-strategy',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(),
      api.properties(exec_strategy='remote'),
      api.post_check(exec_strategy_env_checker),
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
      api.expect_status('INFRA_FAILURE'),
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
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'ensure_verified_mismatches',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(ensure_verified=True),
      api.step_data(
          'postprocess for reclient.load rbe_metrics.pb',
          api.file.read_raw(
              content=MakeTestRBEStats(
                  num_records=1,
                  total_verified=1,
                  total_mismatches=1,
                  mismatches=[
                      log_pb.Verification.Mismatch(
                          path="abc/xyz",
                          local_digests=["11111"],
                          remote_digests=["22222"],
                      )
                  ]))),
      api.post_process(post_process.StepException,
                       'postprocess for reclient.verification'),
      api.post_process(
          post_process.Filter('postprocess for reclient.verification')),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'ensure_verified_mismatches_ignored',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(ensure_verified=True),
      api.step_data(
          'postprocess for reclient.load rbe_metrics.pb',
          api.file.read_raw(
              content=MakeTestRBEStats(
                  num_records=1,
                  total_verified=1,
                  total_mismatches=1,
                  total_ignored_mismatches=1))),
      api.post_process(post_process.StepSuccess,
                       'postprocess for reclient.verification'),
      api.post_process(
          post_process.Filter('postprocess for reclient.verification')),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ensure_verified_mismatches_fallbacks',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(ensure_verified=True),
      api.step_data(
          'postprocess for reclient.load rbe_metrics.pb',
          api.file.read_raw(
              content=MakeTestRBEStats(
                  num_records=1,
                  total_verified=1,
                  total_mismatches=1,
                  mismatches=[
                      log_pb.Verification.Mismatch(
                          path="abc/xyz", local_digests=["123456"])
                  ]))),
      api.post_process(post_process.StepSuccess,
                       'postprocess for reclient.verification'),
      api.post_process(
          post_process.Filter('postprocess for reclient.verification')),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ensure_verified_mismatches_fallbacks_ignored',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(ensure_verified=True),
      api.step_data(
          'postprocess for reclient.load rbe_metrics.pb',
          api.file.read_raw(
              content=MakeTestRBEStats(
                  num_records=2,
                  total_verified=2,
                  total_mismatches=2,
                  total_ignored_mismatches=1,
                  mismatches=[
                      log_pb.Verification.Mismatch(
                          path="abc1/xyz",
                          local_digests=["123456"],
                          ignored=True),
                      log_pb.Verification.Mismatch(
                          path="abc2/xyz",
                          local_digests=["123456"],
                          remote_digests=["999999"])
                  ]))),
      api.post_process(post_process.StepException,
                       'postprocess for reclient.verification'),
      api.post_process(
          post_process.Filter('postprocess for reclient.verification')),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'ensure_broken_rbestats_proto_gets_caught',
      api.buildbucket.ci_build(project='chromium', builder='Linux reclient'),
      api.reclient.properties(),
      api.step_data(
          'postprocess for reclient.load rbe_metrics.pb',
          api.file.read_raw(
              content="corrupt_rbestats_foobar_this_is_not_a_proto")),
      api.post_process(
          post_process.StepFailure,
          'postprocess for reclient.upload RBE metrics to BigQuery (FAILED)'),
      api.expect_status('SUCCESS'),
      api.post_process(post_process.DropExpectation),
  )

  def version_number_checker(check, steps):
    step = steps['preprocess for reclient.get reclient version']
    check(step.logs['reclient_version'] == api.json.dumps(
        {
            'MAJOR': 1,
            'MINOR': 123,
            'PATCH': 2,
            'HASH': 'abcdefgh'
        }, indent=2))

  yield api.test(
      'ensure_version_number_is_parsed',
      api.reclient.properties(),
      api.platform('win', 64),
      api.override_step_data(
          'preprocess for reclient.get reclient version.call bootstrap --version',
          stdout=api.raw_io.output_text('Version: 1.123.2.abcdefgh')),
      api.post_check(version_number_checker),
      api.post_process(post_process.DropExpectation),
  )

  def canonicalize_working_dir_checker(check, steps):
    env = steps[_NINJA_STEP_NAME].env
    check(env['RBE_canonicalize_working_dir'] == 'false')

  yield api.test(
      'ensure_canonicalize_working_dir_disabled_on_windows_pre_109',
      api.reclient.properties(),
      api.platform('win', 64),
      api.override_step_data(
          'preprocess for reclient.get reclient version.call bootstrap --version',
          stdout=api.raw_io.output_text('Version: 0.108.0.abcdefgh')),
      api.post_check(canonicalize_working_dir_checker),
      api.post_process(post_process.DropExpectation),
  )
