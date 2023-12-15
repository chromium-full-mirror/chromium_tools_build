# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, MustRun, StepEnvContains

DEPS = [
    'goma',
    'recipe_engine/buildbucket',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api):
  api.path['checkout'] = api.path['cache'] / 'builder' / 'src'

  api.goma.ensure_goma()
  api.goma.build_with_goma(
      ['ninja', '-C', api.path['checkout'].join('out', 'Release')],
      goma_env=api.m.properties.get('env'),
  )

  api.step('jsonstatus', [])
  api.step.active_result.presentation.logs['details'] = [
      'jsonstatus: %r' % api.goma.jsonstatus,
  ]


def GenTests(api):

  def EnsureGomaSteps():
    return (
        api.post_process(MustRun, 'ensure_goma.ensure_installed'),
        api.post_process(MustRun, 'preprocess_for_goma.goma cache directory'),
        api.post_process(MustRun, 'preprocess_for_goma.start_goma'),
        api.post_process(MustRun, 'preprocess_for_goma.start cloudtail'),
        api.post_process(MustRun, 'compile'),
        api.post_process(MustRun, 'postprocess_for_goma.goma_jsonstatus'),
        api.post_process(MustRun, 'postprocess_for_goma.goma_stat'),
        api.post_process(MustRun, 'postprocess_for_goma.stop_goma'),
        api.post_process(MustRun, 'start goma canceller'),
        api.post_process(MustRun, 'postprocess_for_goma.upload_log'),
        api.post_process(MustRun, 'postprocess_for_goma.stop cloudtail'),
    )

  def EnsureGomaEnv(expected_env):
    return (
        api.post_process(StepEnvContains, 'preprocess_for_goma.start_goma',
                         expected_env),
        api.post_process(StepEnvContains,
                         'postprocess_for_goma.goma_jsonstatus', expected_env),
        api.post_process(StepEnvContains, 'postprocess_for_goma.goma_stat',
                         expected_env),
        api.post_process(StepEnvContains, 'postprocess_for_goma.stop_goma',
                         expected_env),
    )

  yield api.test(
      'basic',
      api.buildbucket.ci_build(),
      *EnsureGomaSteps(),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'enable_ats',
      api.goma(enable_ats=True),
      api.buildbucket.ci_build(),
      *EnsureGomaSteps(),
      *EnsureGomaEnv({'GOMA_ARBITRARY_TOOLCHAIN_SUPPORT': 'true'}),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'server_host',
      api.goma(server_host="goma.chromium.org", rpc_extra_params="?prod"),
      api.buildbucket.ci_build(),
      *EnsureGomaSteps(),
      *EnsureGomaEnv({
          'GOMA_RPC_EXTRA_PARAMS': '?prod',
          'GOMA_SERVER_HOST': 'goma.chromium.org'
      }),
      api.post_process(DropExpectation),
  )
