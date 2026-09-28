# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test to ensure the validity of the entries within TRYBOTS.

Each entry in the TRYBOTS dict will be checked to ensure
chromium_tests.trybot_steps can be called with the input set as it would
be when the try builder runs.
"""

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium_tests,
  chromium_tests_builder_config,
  filter as filter_module,
)
from RECIPE_MODULES.recipe_engine import properties, step


@dataclass
class DEPS(RecipeScriptApi):
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  filter: filter_module.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  builder_id, builder_config = (
    api.chromium_tests_builder_config.lookup_builder()
  )
  api.chromium_tests.trybot_steps(builder_id, builder_config)
  api.step('Success', ['echo', 'Success!'])


def GenTests(api: TEST_DEPS):
  for builder_id in sorted(ctbc.trybots.TRYBOTS):
    builder_group = builder_id.group
    buildername = builder_id.builder
    yield api.test(
      ('%s-%s' % (builder_group, buildername)).replace(' ', '_'),
      (
        api.properties(xcode_build_version='11c29')
        if 'ios' in buildername
        else api.properties()
      ),
      api.chromium_tests_builder_config.try_build(
        builder_group=builder_group,
        builder=buildername,
        patch_set=1,
      ),
      # Suppress analysis so that all targets show up as affected and we run
      # recipe code for each configured test
      api.post_process(post_process.DropExpectation),
    )
