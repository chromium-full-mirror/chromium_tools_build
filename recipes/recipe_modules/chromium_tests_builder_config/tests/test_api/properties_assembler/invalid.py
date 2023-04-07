# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test recipe for invalid use of property builders of the test API."""

from recipe_engine import post_process

DEPS = [
    'chromium_tests_builder_config',
    'recipe_engine/assertions',
    'recipe_engine/json',
    'recipe_engine/properties',
]


def RunSteps(api):
  ctbc_test_api = api.chromium_tests_builder_config.test_api

  # Building tester without specifying parent
  props_assembler = ctbc_test_api.properties_assembler_for_ci_tester(
      builder_group='fake-group', builder='fake-tester')
  with api.assertions.assertRaises(TypeError) as caught:
    props_assembler.assemble()
  api.assertions.assertEqual(
      str(caught.exception),
      '`with_parent` must be called before calling `assemble`')

  # Specifying parent multiple times
  props_assembler = ctbc_test_api \
      .properties_assembler_for_ci_tester(
          builder_group='fake-group', builder='fake-tester') \
      .with_parent(builder_group='fake-group', builder='fake-builder')
  with api.assertions.assertRaises(TypeError) as caught:
    props_assembler.with_parent(
        builder_group='fake-group', builder='fake-builder')
  api.assertions.assertEqual(
      str(caught.exception), '`with_parent` can only be called once')

  # Building try builder without specifying mirrored builder
  props_assembler = ctbc_test_api.properties_assembler_for_try_builder()
  with api.assertions.assertRaises(TypeError) as caught:
    props_assembler.assemble()
  api.assertions.assertEqual(
      str(caught.exception),
      '`with_mirrored_builder` must be called before calling `assemble`')

  # Adding a mirrored tester without specifying mirrored builder
  props_assembler = ctbc_test_api.properties_assembler_for_try_builder()
  with api.assertions.assertRaises(TypeError) as caught:
    props_assembler.with_mirrored_tester()
  api.assertions.assertEqual(
      str(caught.exception), ('`with_mirrored_builder` must be called'
                              ' before calling `with_mirrored_tester`'))


def GenTests(api):
  yield api.test(
      'full',
      api.post_process(post_process.DropExpectation),
  )
