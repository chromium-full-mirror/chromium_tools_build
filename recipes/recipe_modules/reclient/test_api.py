# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api


class ReclientTestApi(recipe_test_api.RecipeTestApi):
  def properties(
    self,
    instance='test-rbe-project',
    metrics_project=None,
    rewrapper_env=None,
    bootstrap_env=None,
    profiler_service=None,
    publish_trace=None,
    scandeps_server=False,
    cache_silo=None,
    ensure_verified=None,
    disable_bq_upload=False,
  ):
    if rewrapper_env is None:
      rewrapper_env = {}
    if bootstrap_env is None:
      bootstrap_env = {}

    return self.m.properties(
      **{
        '$build/reclient': {
          'instance': instance,
          'metrics_project': metrics_project,
          'rewrapper_env': rewrapper_env,
          'bootstrap_env': bootstrap_env,
          'profiler_service': profiler_service,
          'publish_trace': publish_trace,
          'scandeps_server': scandeps_server,
          'cache_silo': cache_silo,
          'ensure_verified': ensure_verified,
          'disable_bq_upload': disable_bq_upload,
        },
      }
    )
