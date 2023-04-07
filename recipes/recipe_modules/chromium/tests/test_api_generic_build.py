# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'builder_group',
    'chromium',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/properties',
]


def RunSteps(api):
  api.assertions.assertFalse(
      api.buildbucket.build.input.HasField('gitiles_commit'))
  api.assertions.assertEqual(api.builder_group.for_current, 'fake-group')
  api.assertions.assertEqual(api.builder_group.for_parent,
                             api.properties['expected_parent_builder_group'])
  api.assertions.assertEqual(
      api.properties.get('parent_buildername', None),
      api.properties['expected_parent_buildername'])


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium.generic_build(builder_group='fake-group'),
      api.properties(
          expected_parent_builder_group=None,
          expected_parent_buildername=None,
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'parent-builder',
      api.chromium.generic_build(
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
      api.chromium.generic_build(
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
