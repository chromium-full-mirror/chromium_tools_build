# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import siso

DEPS = [
    'chromium',
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
    'siso',
]


def RunSteps(api):
  source_dir = api.path.cache_dir / 'builder' / 'src'
  build_dir = api.path.join('out', 'Default')
  src_cfg = api.gclient.make_config(CACHE_DIR=api.path.cache_dir / 'git')
  src_cfg.solutions.add()
  api.gclient.c = src_cfg
  env = {}
  if api.siso.enabled:
    # for code coverage
    _ = api.siso.project
    _ = api.siso.reapi_instance

    api.siso.enable_download_remoteexec_cfg_hook()
    api.path.mock_add_paths(
        api.path.join('out', 'Release', 'siso_metrics.json'))
    api.siso.check_version(source_dir)
    api.siso.run_ninja(
        source_dir,
        ninja_command=api.properties.get('build_command'),
        ninja_env=env,
        name=api.properties.get('name', 'compile'),
        siso_args=api.properties.get('siso_args'),
        resource_usage_output_file=api.properties.get(
            'resource_usage_output_file', None),
        post_step_func=lambda s: s,
        ninja_invocation_id='some_random_id',
        include_utr_instruction=True,
        builder_id=chromium_types.BuilderId.create_for_group(
            'builder group', 'builder name'))
    with api.siso.context():
      api.step('siso context', cmd=['some_process_calling_siso.py'])
  with api.siso.disable():
    assert not api.siso.enabled
  if api.siso.without_bytes:
    api.siso.isolate_tests('isolate tests', source_dir, build_dir,
                           api.properties.get('tests'))
    for t in api.properties.get('tests', []):
      api.siso.fs_flush(
          f'fs flush {t}',
          source_dir,
          [f'{t}.exe'],
      )


def GenTests(api):

  def siso_env_checker(check, steps):
    env = steps['siso context'].env
    check(env['SISO_PROJECT'] == 'test-rbe-project')

  yield api.test(
      'basic',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(),
      api.post_check(siso_env_checker),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'siso_args',
      api.properties(
          build_command=['ninja', '-C', 'out/Release'], siso_args=['-foo']),
      api.siso.properties(),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'reapi_address',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(
          reapi_address='us-east1-remotebuildexecution.googleapis.com:443'),
      api.post_process(post_process.StepCommandContains, 'compile', [
          '--reapi_address', 'us-east1-remotebuildexecution.googleapis.com:443'
      ]),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'action_salt',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(action_salt='xxx'),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--action_salt', 'xxx']),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'experiments',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(experiments=['no-file-access-trace']),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'local_profiler',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(profile_mode='local'),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['-cpuprofile']),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'cloud_profiler',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(profile_mode='cloud'),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--enable_cloud_profiler']),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'cloud_trace',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(enable_cloud_trace=True),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--enable_cloud_trace']),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'cloud_monitoring',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.buildbucket.ci_build(project='chromium', builder='linux-rel'),
      api.siso.properties(
          project='test-rbe-proj',
          enable_cloud_monitoring=True,
      ),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--enable_cloud_monitoring']),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--metrics_project', 'test-rbe-proj']),
      api.post_process(post_process.StepCommandContains, 'compile', [
          '--metrics_labels',
          'type=ci,host_os=linux,project=chromium,bucket=ci,builder=linux-rel,source=prod,tool=siso',
      ]),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'cloud_monitoring_with_custom_metrics_project',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.buildbucket.ci_build(project='chromium', builder='linux-rel'),
      api.siso.properties(
          project='test-rbe-proj',
          enable_cloud_monitoring=True,
          metrics_project='test-metrics-proj',
      ),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--enable_cloud_monitoring']),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--metrics_project', 'test-metrics-proj']),
      api.post_process(post_process.StepCommandContains, 'compile', [
          '--metrics_labels',
          'type=ci,host_os=linux,project=chromium,bucket=ci,builder=linux-rel,source=prod,tool=siso',
      ]),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'configs',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(configs=['foo', 'bar']),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--config', 'foo,bar']),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'remote_jobs',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(remote_jobs=100),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['--remote_jobs', '100']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'keep_going_zero',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(keep_going=0),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['-k', '0']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'keep_going_non_zero',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(keep_going=3),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['-k', '3']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'limits',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(limits='fastlocal=0'),
      api.post_process(post_process.StepEnvContains, 'compile',
                       {'SISO_LIMITS': 'fastlocal=0'}),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'compile_failure',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(action_salt='xxx'),
      api.step_data('compile', retcode=1),
      api.post_process(post_process.StepFailure, 'compile'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
  yield api.test(
      'ninja_no_C',
      api.properties(build_command=['ninja']),
      api.siso.properties(),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'fail_if_reapi_used',
      api.properties(build_command=['ninja']),
      api.siso.properties(fail_if_reapi_used=True),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'resource_usage',
      api.properties(
          build_command=['ninja', '-C', 'out/Release'],
          resource_usage_output_file=api.path.cache_dir /
          'resource_usage.json'),
      api.siso.properties(),
      api.post_process(post_process.StepCommandContains, 'compile', [
          '/usr/bin/time', f'--format={siso.RUSAGE_FORMAT}', '-o',
          '[CACHE]/resource_usage.json',
          '[CACHE]/builder/src/third_party/siso/cipd/siso', 'ninja'
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'report_uplaod_failure',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(),
      api.step_data(
          'upload siso reports.gsutil upload siso_metrics.json', retcode=1),
      api.post_process(post_process.StepException,
                       'upload siso reports.gsutil upload siso_metrics.json'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'output_local_strategy',
      api.properties(
          build_command=['ninja', '-C', 'out/Release'],
          tests=['base_unittests']),
      api.siso.properties(output_local_strategy='minimum'),
      api.post_process(post_process.StepCommandContains, 'compile', [
          '--output_local_strategy',
          'minimum',
      ]),
      api.post_process(post_process.StepCommandContains, 'isolate tests',
                       ['base_unittests']),
      api.post_process(post_process.StepSuccess, 'fs flush base_unittests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'disable_batch_mode',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(disable_batch_mode=True),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['-batch=false']),
      api.post_process(post_process.DropExpectation),
  )
