# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, MustRunRE, DoesNotRunRE

DEPS = [
  'flakiness',
  'recipe_engine/buildbucket',
  'recipe_engine/step',
  'depot_tools/tryserver',
]


def RunSteps(api):
  # Pass affected_files to avoid gclient check crash in tests when it proceeds
  api.flakiness.find_tests_for_flakiness([], affected_files=['some_file.txt'])


def GenTests(api):
  yield api.test(
    'footer_skip',
    api.buildbucket.try_build(project='chromium'),
    api.flakiness(check_for_flakiness=True),
    api.tryserver.get_footers({'Validate-Test-Flakiness': ['Skip']}),
    api.post_process(
      MustRunRE,
      r'.*skipping flaky test check.*',
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'properties_disabled',
    api.buildbucket.try_build(project='chromium'),
    # check_for_flakiness is False by default
    api.post_process(
      DoesNotRunRE,
      r'.*skipping flaky test check.*',
    ),
    api.post_process(DoesNotRunRE, r'.*no test files were detected.*'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'footer_undefined',
    api.buildbucket.try_build(project='chromium'),
    api.flakiness(check_for_flakiness=True),
    # If the footer is undefined (default), but check_for_flakiness=True,
    # the function will NOT skip due to footer, but instead proceed to check
    # test files. Since we pass no affected_test_files (only some_file.txt),
    # it will emit the "no test files" step.
    api.post_process(
      DoesNotRunRE,
      r'.*skipping flaky test check.*',
    ),
    api.post_process(MustRunRE, r'.*no test files were detected.*'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'footer_not_skip',
    api.buildbucket.try_build(project='chromium'),
    api.flakiness(check_for_flakiness=True),
    api.tryserver.get_footers({'Validate-Test-Flakiness': ['Run']}),
    api.post_process(
      DoesNotRunRE,
      r'.*skipping flaky test check.*',
    ),
    api.post_process(MustRunRE, r'.*no test files were detected.*'),
    api.post_process(DropExpectation),
  )
