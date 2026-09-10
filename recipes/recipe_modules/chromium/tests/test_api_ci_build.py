# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import builder_group, chromium
from RECIPE_MODULES.recipe_engine import assertions, buildbucket, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  chromium: chromium.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  api.assertions.assertTrue(
    api.buildbucket.build.input.HasField('gitiles_commit')
  )
  api.assertions.assertEqual(api.builder_group.for_current, 'fake-group')
  api.assertions.assertEqual(
    api.builder_group.for_parent,
    api.properties['expected_parent_builder_group'],
  )
  api.assertions.assertEqual(
    api.properties.get('parent_buildername', None),
    api.properties['expected_parent_buildername'],
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.chromium.ci_build(builder_group='fake-group'),
    api.properties(
      expected_parent_builder_group=None,
      expected_parent_buildername=None,
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'parent-builder',
    api.chromium.ci_build(
      builder_group='fake-group',
      parent_buildername='fake-parent',
    ),
    api.properties(
      expected_parent_builder_group='fake-group',
      expected_parent_buildername='fake-parent',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'parent-builder-and-group',
    api.chromium.ci_build(
      builder_group='fake-group',
      parent_builder_group='fake-parent-group',
      parent_buildername='fake-parent',
    ),
    api.properties(
      expected_parent_builder_group='fake-parent-group',
      expected_parent_buildername='fake-parent',
    ),
    api.post_process(post_process.DropExpectation),
  )
