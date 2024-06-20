# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_test_api


class SisoTestApi(recipe_test_api.RecipeTestApi):

  def properties(self,
                 project='test-rbe-project',
                 reapi_address=None,
                 reapi_instance='default_instance',
                 action_salt=None,
                 experiments=None,
                 enable_cloud_profiler=None,
                 enable_cloud_trace=None,
                 configs=None,
                 remote_jobs=None,
                 fail_if_reapi_used=None):
    return self.m.properties(
        **{
            '$build/siso': {
                'project': project,
                'reapi_address': reapi_address,
                'reapi_instance': reapi_instance,
                'action_salt': action_salt,
                'experiments': experiments,
                'enable_cloud_profiler': enable_cloud_profiler,
                'enable_cloud_trace': enable_cloud_trace,
                'configs': configs,
                'remote_jobs': remote_jobs,
                'fail_if_reapi_used': fail_if_reapi_used,
            },
        })
