# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DropExpectation, MustRun, StatusSuccess,
                                        StepEnvContains)

DEPS = [
    'goma',
]


def RunSteps(api):
  api.goma.ensure_goma(client_type='candidate')
  api.goma.configure_enable_ats()
  api.goma.start()


def GenTests(api):

  yield api.test(
      'basic',
      api.goma(server_host='goma.chromium.org', rpc_extra_params="?prod"),
      api.post_process(
          MustRun, 'goma.configure_enable_ats: goma.chromium.org ?prod True'),
      api.post_process(
          StepEnvContains, 'preprocess_for_goma.start_goma', {
              'GOMA_ARBITRARY_TOOLCHAIN_SUPPORT': 'true',
              'GOMA_RPC_EXTRA_PARAMS': '?prod',
              'GOMA_SERVER_HOST': 'goma.chromium.org',
          }),
      api.post_process(StatusSuccess),
      api.post_process(DropExpectation),
  )
