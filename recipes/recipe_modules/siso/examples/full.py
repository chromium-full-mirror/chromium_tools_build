# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'recipe_engine/path',
    'recipe_engine/properties',
    'siso',
]


def RunSteps(api):
  source_dir = api.path.cache_dir / 'builder' / 'src'
  env = {}
  if api.siso.enabled:
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
    )
  with api.siso.disable():
    assert not api.siso.enabled


def GenTests(api):
  yield api.test(
      'basic',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(),
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
      'cloud_profiler',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(enable_cloud_profiler=True),
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
          '/usr/bin/time', '--format={"ru_utime": %U}', '-o',
          '[CACHE]/resource_usage.json',
          '[CACHE]/builder/src/third_party/siso/siso', 'ninja'
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'report_uplaod_failure',
      api.properties(build_command=['ninja', '-C', 'out/Release'],),
      api.siso.properties(),
      api.step_data(
          'upload siso reports.gsutil upload siso_metrics.json', retcode=1),
      api.post_process(post_process.StepWarning,
                       'upload siso reports.gsutil upload siso_metrics.json'),
      api.post_process(post_process.DropExpectation),
  )
