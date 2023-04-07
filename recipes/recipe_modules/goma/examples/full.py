# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium',
    'goma',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
]

from recipe_engine import post_process

def RunSteps(api):
  env = {}

  api.goma.ensure_goma(api.properties.get('client_type'))

  api.step('gn', ['gn', 'gen', 'out/Release',
                  '--args=use_goma=true goma_dir=%s' % api.goma.goma_dir])

  command = list(api.properties.get('build_command'))
  if api.properties.get('custom_tmp_dir'):
    env['GOMA_TMP_DIR'] = api.properties.get('custom_tmp_dir')

  command += ['-j', str(api.goma.jobs)]

  api.goma.build_with_goma(
      name='ninja',
      ninja_log_outdir=api.properties.get('ninja_log_outdir'),
      ninja_log_compiler=api.properties.get('ninja_log_compiler'),
      ninja_command=command,
      goma_env=env)


def GenTests(api):
  common_test_data = sum([
      api.chromium.ci_build(builder_group='test_group'),
      api.properties(
          build_command=['ninja', '-C', 'out/Release'],
          ninja_log_outdir='out/Release',
          ninja_log_compiler='goma',
          build_data_dir='build_data_dir',
      ),
  ], api.empty_test_data())

  for platform in ('linux', 'win', 'mac'):
    yield api.test(
        platform,
        common_test_data,
        api.platform.name(platform),
    )

  yield api.test(
      'linux_custom_jobs',
      common_test_data,
      api.platform.name('linux'),
      api.goma(jobs=80),
  )

  yield api.test(
      'linux_debug',
      common_test_data,
      api.platform.name('linux'),
      api.goma(jobs=80, debug=True),
  )

  yield api.test(
      'linux_compile_failed',
      common_test_data,
      api.platform.name('linux'),
      api.step_data('ninja', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'linux_start_goma_failed',
      common_test_data,
      api.platform.name('linux'),
      api.step_data('preprocess_for_goma.start_goma', retcode=1),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'linux_stop_goma_failed',
      common_test_data,
      api.platform.name('linux'),
      api.step_data('postprocess_for_goma.stop_goma', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'linux_set_custome_tmp_dir',
      common_test_data,
      api.platform.name('linux'),
      api.properties(custom_tmp_dir='/tmp/goma_goma_module'),
  )

  yield api.test(
      'linux_invalid_goma_jsonstatus',
      common_test_data,
      api.platform.name('linux'),
      api.step_data('postprocess_for_goma.goma_jsonstatus',
                    api.json.output(data=None)),
  )

  yield api.test(
      'linux_local_run_goma_recipe',
      common_test_data,
      api.platform.name('linux'),
      api.properties(**{"$build/goma": {
          "local": "[START_DIR]/goma",
      }}),
  )

  yield api.test(
      'win_goma_canary',
      common_test_data,
      api.platform.name('win'),
      api.properties(client_type='candidate'),
  )

  yield api.test(
      'win_goma_latest_client',
      common_test_data,
      api.platform.name('win'),
      api.properties(client_type='latest'),
      api.post_process(post_process.MustRun, 'ensure_goma'),
      api.post_process(post_process.StepTextContains, 'ensure_goma',
                       ['latest']),
  )
