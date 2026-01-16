# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_test_api


class SisoTestApi(recipe_test_api.RecipeTestApi):

  def properties(self,
                 project='test-rbe-project',
                 reapi_address=None,
                 reapi_instance='default_instance',
                 action_salt=None,
                 experiments=None,
                 profile_mode=None,
                 enable_cloud_trace=None,
                 enable_cloud_monitoring=None,
                 metrics_project=None,
                 configs=None,
                 remote_jobs=None,
                 limits=None,
                 fail_if_reapi_used=None,
                 output_local_strategy=None,
                 keep_going=None,
                 disable_batch_mode=None):
    return self.m.properties(
        **{
            '$build/siso': {
                'project': project,
                'reapi_address': reapi_address,
                'reapi_instance': reapi_instance,
                'action_salt': action_salt,
                'experiments': experiments,
                'profile_mode': profile_mode,
                'enable_cloud_trace': enable_cloud_trace,
                'enable_cloud_monitoring': enable_cloud_monitoring,
                'metrics_project': metrics_project,
                'configs': configs,
                'remote_jobs': remote_jobs,
                'limits': limits,
                'fail_if_reapi_used': fail_if_reapi_used,
                'output_local_strategy': output_local_strategy,
                'keep_going': keep_going,
                'disable_batch_mode': disable_batch_mode,
            },
        })
