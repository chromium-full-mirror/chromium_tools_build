# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Packages Android AVDs as CIPD packages."""

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import avd_packager
from RECIPE_MODULES.recipe_engine import buildbucket, json, properties


@dataclass
class DEPS(RecipeScriptApi):
  avd_packager: avd_packager.API
  buildbucket: buildbucket.API
  json: json.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  assert (not api.buildbucket.build.input.gerrit_changes
         ), "CI builders must not run with gerrit_changes"
  api.avd_packager.prepare()
  api.avd_packager.execute()


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.properties(
          **{
              '$build/avd_packager': {
                  'avd_configs': ['some/proto/foo.textpb',],
                  'gclient_config': 'chromium',
                  'gclient_apply_config': ['android'],
              },
          }),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.List AVD'),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.Create AVD'),
      api.post_process(post_process.MustRun,
                       'Process some/proto/foo.textpb.Uninstall AVD'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'gerrit_changes_rejected',
      api.buildbucket.try_build(),
      api.expect_exception('AssertionError'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
