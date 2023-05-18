# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build.attr_utils import attrib, attrs
from RECIPE_MODULES.build.chromium_tests import steps

DEPS = [
    'chromium_tests',
    'recipe_engine/assertions',
    'recipe_engine/step',
]


def RunSteps(api):

  @attrs()
  class FakeTestSpec(steps.TestSpec):

    supports_rts = attrib(bool, default=False)
    supports_inverted_rts = attrib(bool, default=False)

    @property
    def test_class(self):
      return FakeTest

  class FakeTest(steps.Test):

    def compile_targets(self):
      return []  # pragma: no cover

    @property
    def supports_rts(self):
      return self.spec.supports_rts

    @property
    def supports_inverted_rts(self):
      return self.spec.supports_inverted_rts

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

  for supports_rts in (True, False):
    spec = WrapperSpec.create(
        FakeTestSpec.create('fake-test', supports_rts=supports_rts))
    test = spec.get_test(api.chromium_tests)
    api.assertions.assertEqual(test.supports_rts, supports_rts)

  for is_rts in (True, False):
    spec = WrapperSpec.create(
        FakeTestSpec.create('fake-test', supports_rts=True))
    test = spec.get_test(api.chromium_tests)
    test.is_rts = is_rts
    api.assertions.assertEqual(test.is_rts, is_rts)

  for supports_inverted_rts in (True, False):
    spec = WrapperSpec.create(
        FakeTestSpec.create(
            'fake-test', supports_inverted_rts=supports_inverted_rts))
    test = spec.get_test(api.chromium_tests)
    api.assertions.assertEqual(test.supports_inverted_rts,
                               supports_inverted_rts)

  for is_inverted_rts in (True, False):
    spec = WrapperSpec.create(
        FakeTestSpec.create('fake-test', supports_inverted_rts=True))
    test = spec.get_test(api.chromium_tests)
    test.is_inverted_rts = is_inverted_rts
    api.assertions.assertEqual(test.is_inverted_rts, is_inverted_rts)


def GenTests(api):
  yield api.test(
      'full',
      api.post_process(post_process.DropExpectation),
  )
