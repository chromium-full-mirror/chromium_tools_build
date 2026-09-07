# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.attr_utils import attrib, attrs
from RECIPE_MODULES.build.chromium_tests import steps

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_tests
from RECIPE_MODULES.recipe_engine import assertions, step


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium_tests: chromium_tests.API
  step: step.API


def RunSteps(api: DEPS):

  @attrs()
  class FakeTestSpec(steps.TestSpec):

    @property
    def test_class(self):
      return FakeTest

  class FakeTest(steps.Test):

    @property
    def locality(self) -> steps.TestLocality:
      return steps.TestLocality.LOCAL

    def compile_targets(self):
      return []  # pragma: no cover

    def pre_run(self, suffix: str):
      pass  # pragma: no cover

    def run(self, suffix: str):
      return api.step.empty('fake test step')  # pragma: no cover

    def get_invocation_names(self, suffix: str):
      return []  # pragma: no cover

    def notrun_failures(self, suffix):
      return [f'{suffix}-notrun-failure']

  class WrapperSpec(steps.TestWrapperSpec):

    @property
    def test_wrapper_class(self):
      return Wrapper

  class Wrapper(steps.TestWrapper):
    pass

  test_spec = FakeTestSpec.create('foo-test')
  wrapped_spec = WrapperSpec.create(test_spec)

  test = wrapped_spec.get_test(api.chromium_tests)

  api.assertions.assertTrue(test.runs_locally)

  test_options = steps.TestOptions.create(test_filter=['foo', 'bar'])
  test.test_options = test_options
  api.assertions.assertEqual(test.test_options, test_options)

  for check_flakiness_for_new_tests in (True, False):
    spec = WrapperSpec.create(
        FakeTestSpec.create(
            'fake-test',
            check_flakiness_for_new_tests=check_flakiness_for_new_tests))
    test = spec.get_test(api.chromium_tests)
    api.assertions.assertEqual(test.check_flakiness_for_new_tests,
                               check_flakiness_for_new_tests)

  api.assertions.assertCountEqual(
      test.notrun_failures('foo'), ['foo-notrun-failure'])

  test.add_known_luci_analysis_flaky_failures(
      ['known-flaky-failure-1', 'known-flaky-failure-2'])
  api.assertions.assertCountEqual(
      test.known_luci_analysis_flaky_failures,
      ['known-flaky-failure-1', 'known-flaky-failure-2'])

  test.add_weak_luci_analysis_flaky_failure('weak-flaky-failure-1')
  test.add_weak_luci_analysis_flaky_failure('weak-flaky-failure-2')
  api.assertions.assertCountEqual(
      test.weak_luci_analysis_flaky_failures,
      ['weak-flaky-failure-1', 'weak-flaky-failure-2'])

  test.raw_cmd = 'fake-raw-cmd'
  api.assertions.assertEqual(test.raw_cmd, 'fake-raw-cmd')

  test.relative_cwd = 'fake-relative-cwd'
  api.assertions.assertEqual(test.relative_cwd, 'fake-relative-cwd')


def GenTests(api: RecipeTestApi):
  yield api.test(
      'full',
      api.post_process(post_process.DropExpectation),
  )
