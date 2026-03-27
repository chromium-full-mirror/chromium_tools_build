# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections

from recipe_engine import post_process

DEPS = [
    'flakiness',
    'recipe_engine/assertions',
]


def RunSteps(api):
  # test_id = None
  api.assertions.assertEqual(
      api.flakiness._get_legacy_filter_from_test_id(None), None)

  # test_id = ""
  api.assertions.assertEqual(
      api.flakiness._get_legacy_filter_from_test_id(""), None)

  # doesn't start with ://
  api.assertions.assertEqual(
      api.flakiness._get_legacy_filter_from_test_id("invalid"), None)

  # Doesn't match regex.
  test_id = ("://chrome/test/chromedriver:chromedriver_py_tests!pyunit"
             "ChromeDriverW3cTest#testSendKeys")
  api.assertions.assertEqual(
      api.flakiness._get_legacy_filter_from_test_id(test_id), None)

  # unsupported scheme
  test_id = "://module!nothing:coarse:fine#case"
  api.assertions.assertEqual(
      api.flakiness._get_legacy_filter_from_test_id(test_id), None)

  # pyunit success
  test_id = ("://chrome/test/chromedriver:chromedriver_py_tests!pyunit:"
             "__main__:ChromeDriverW3cTest#testSendKeys")
  api.assertions.assertEqual(
      api.flakiness._get_legacy_filter_from_test_id(test_id),
      "__main__.ChromeDriverW3cTest.testSendKeys")

  test_id_2 = r"://\:blink_python_tests!pyunit:blinkbuild.name_style_converter_test:NameStyleConverterTest#test_all_cases"
  api.assertions.assertEqual(
      api.flakiness._get_legacy_filter_from_test_id(test_id_2),
      "blinkbuild.name_style_converter_test.NameStyleConverterTest.test_all_cases"
  )

  test_id_3 = r"://m!pyunit:c:f#case\\name"
  api.assertions.assertEqual(
      api.flakiness._get_legacy_filter_from_test_id(test_id_3),
      r"c.f.case\name")

  # unescape ':'
  test_id_4 = r"://m!pyunit:c:f#case\:name"
  api.assertions.assertEqual(
      api.flakiness._get_legacy_filter_from_test_id(test_id_4), "c.f.case:name")

  # test _map_test_object covers line 589
  IndividualTest = collections.namedtuple(
      'IndividualTest', ['test_id', 'test_name', 'duration_milliseconds'])
  Results = collections.namedtuple('Results', ['variant_hash', 'all_tests'])

  class MockTest:

    def __init__(self, variant_hash, test_id):
      self.check_flakiness_for_new_tests = True
      self.name = "suite"
      self._results = Results(variant_hash,
                              [IndividualTest(test_id, "legacy_name", 100)])

    def get_rdb_results(self, suffix):
      return self._results

  test_id_pyunit = "://m!pyunit:c:f#case"
  vh = "vh"
  test_obj = MockTest(vh, test_id_pyunit)
  results = api.flakiness._map_test_object([test_obj], [(test_id_pyunit, vh)])
  api.assertions.assertEqual(results[test_obj], (["c.f.case"], 100))


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
