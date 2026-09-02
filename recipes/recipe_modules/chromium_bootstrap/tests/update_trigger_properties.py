# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.engine_types import thaw

from PB.go.chromium.org.luci.buildbucket.proto import common

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_bootstrap
from RECIPE_MODULES.recipe_engine import assertions, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_bootstrap: chromium_bootstrap.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_bootstrap: chromium_bootstrap.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  trigger_props = {'foo': 'bar'}
  api.chromium_bootstrap.update_trigger_properties(trigger_props)
  api.assertions.assertEqual(trigger_props,
                             thaw(api.properties['expected_properties']))


def GenTests(api: TEST_DEPS):

  def expect_properties(properties):
    return sum([
        api.properties(expected_properties=properties),
        api.post_process(post_process.DropExpectation),
    ], api.empty_test_data())

  yield api.test(
      'not-bootstrapped',
      expect_properties({'foo': 'bar'}),
  )

  commits = [
      common.GitilesCommit(
          host='chromium.googlesource.com',
          project='chromium/src',
          ref='refs/heads/main',
          id='src-hash',
      ),
      common.GitilesCommit(
          host='chrome-internal.googlesource.com',
          project='chrome/src-internal',
          ref='refs/heads/main',
          id='src-internal-hash',
      ),
  ]

  yield api.test(
      'bootstrapped',
      api.chromium_bootstrap.properties(commits=commits),
      expect_properties({
          'foo': 'bar',
          '$bootstrap/trigger': {
              'commits': [
                  {
                      'host': 'chromium.googlesource.com',
                      'project': 'chromium/src',
                      'ref': 'refs/heads/main',
                      'id': 'src-hash',
                  },
                  {
                      'host': 'chrome-internal.googlesource.com',
                      'project': 'chrome/src-internal',
                      'ref': 'refs/heads/main',
                      'id': 'src-internal-hash',
                  },
              ],
          },
      }),
  )
