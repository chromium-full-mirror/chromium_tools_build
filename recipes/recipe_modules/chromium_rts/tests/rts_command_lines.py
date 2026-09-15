# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests.steps import MockTestSpec

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_rts, chromium_tests
from RECIPE_MODULES.recipe_engine import file, path, properties


@dataclass
class DEPS(RecipeScriptApi):
  chromium_rts: chromium_rts.API
  chromium_tests: chromium_tests.API
  file: file.API
  path: path.API
  properties: properties.API


def RunSteps(api: DEPS):
  spec = MockTestSpec.create(
    name='blink_web_tests',
    runs_on_swarming=True,
    enable_rts_filtering=True,
  )
  test = spec.get_test(api.chromium_tests)
  messages = []
  api.chromium_rts.append_test_step_text(test, messages)
  assert not messages

  api.chromium_rts.set_swarming_test_execution_info(
    test, {'rts': {'blink_web_tests': ['/bin/rts_cmd']}}
  )
  assert test.raw_cmd == ['/bin/rts_cmd']
  api.chromium_rts.append_test_step_text(test, messages)
  assert messages == [
    'Ran tests selected by Regression Test Selection (RTS).\n'
  ]
  api.chromium_rts.set_swarming_test_execution_info(test, None)

  # Test non-allowlisted suite
  spec2 = MockTestSpec.create(name='other_tests', runs_on_swarming=True)
  test2 = spec2.get_test(api.chromium_tests)
  api.chromium_rts.set_swarming_test_execution_info(
    test2, {'rts': {'other_tests': ['/bin/rts_cmd']}}
  )
  assert test2.raw_cmd != ['/bin/rts_cmd']

  # Test derivative suite sharing target_name with parent suite
  parent_spec = MockTestSpec.create(
    name='interactive_ui_tests',
    target_name='interactive_ui_tests',
    runs_on_swarming=True,
    enable_rts_filtering=True,
  )
  parent_test = parent_spec.get_test(api.chromium_tests)

  derivative_spec = MockTestSpec.create(
    name='pixel_interactive_ui_tests',
    target_name='interactive_ui_tests',
    runs_on_swarming=True,
    enable_rts_filtering=False,
  )
  derivative_test = derivative_spec.get_test(api.chromium_tests)

  variants = {
    'rts': {
      'interactive_ui_tests': ['/bin/interactive_ui_tests', '--filter=...']
    }
  }
  api.chromium_rts.set_swarming_test_execution_info(parent_test, variants)
  api.chromium_rts.set_swarming_test_execution_info(derivative_test, variants)

  # Parent suite should have overwritten command and step text
  assert parent_test.raw_cmd == ['/bin/interactive_ui_tests', '--filter=...']
  parent_messages = []
  api.chromium_rts.append_test_step_text(parent_test, parent_messages)
  assert parent_messages == [
    'Ran tests selected by Regression Test Selection (RTS).\n'
  ]

  # Derivative suite should NOT have overwritten command or step text
  assert derivative_test.raw_cmd != [
    '/bin/interactive_ui_tests',
    '--filter=...',
  ]
  derivative_messages = []
  api.chromium_rts.append_test_step_text(derivative_test, derivative_messages)
  assert not derivative_messages

  # Verify get_filter_file_path for parent vs derivative suites
  assert api.chromium_rts.get_filter_file_path(
    api.path.cleanup_dir, parent_test
  ) == (api.path.cleanup_dir / 'gen' / 'rts' / 'interactive_ui_tests.filter')
  assert api.chromium_rts.get_filter_file_path(
    api.path.cleanup_dir, parent_test, inverted=True
  ) == (
    api.path.cleanup_dir
    / 'gen'
    / 'rts'
    / 'interactive_ui_tests_inverted.filter'
  )
  assert (
    api.chromium_rts.get_filter_file_path(api.path.cleanup_dir, derivative_test)
    is None
  )
  assert (
    api.chromium_rts.get_filter_file_path(
      api.path.cleanup_dir, derivative_test, inverted=True
    )
    is None
  )

  derivative_with_rts = MockTestSpec.create(
    name='pixel_interactive_ui_tests',
    target_name='interactive_ui_tests',
    runs_on_swarming=True,
    enable_rts_filtering=True,
  ).get_test(api.chromium_tests)
  assert api.chromium_rts.get_filter_file_path(
    api.path.cleanup_dir, derivative_with_rts
  ) == (
    api.path.cleanup_dir / 'gen' / 'rts' / 'pixel_interactive_ui_tests.filter'
  )
  assert api.chromium_rts.get_filter_file_path(
    api.path.cleanup_dir, derivative_with_rts, inverted=True
  ) == (
    api.path.cleanup_dir
    / 'gen'
    / 'rts'
    / 'pixel_interactive_ui_tests_inverted.filter'
  )

  # Verify get_command_line_variants when target is not in command_lines
  test_missing_cmd = MockTestSpec.create(
    name='missing_cmd_tests',
    runs_on_swarming=True,
    enable_rts_filtering=True,
  ).get_test(api.chromium_tests)
  assert (
    api.chromium_rts.get_command_line_variants(
      api.path.cleanup_dir, {}, [test_missing_cmd]
    )
    == {}
  )

  # Verify _get_isolate_target fallback logic
  assert (
    api.chromium_rts._get_isolate_target(parent_test) == 'interactive_ui_tests'
  )
  assert (
    api.chromium_rts._get_isolate_target(derivative_test)
    == 'interactive_ui_tests'
  )

  class ExplicitIsolateTest(parent_test.__class__):
    @property
    def isolate_target(self):
      return 'explicit_isolate'

  test_with_explicit_isolate = ExplicitIsolateTest(
    parent_spec, api.chromium_tests
  )
  assert (
    api.chromium_rts._get_isolate_target(test_with_explicit_isolate)
    == 'explicit_isolate'
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'set_swarming_test_execution_info',
    api.post_process(post_process.DropExpectation),
  )
