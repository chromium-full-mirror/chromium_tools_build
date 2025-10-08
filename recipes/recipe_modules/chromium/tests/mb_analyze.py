# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
import textwrap

DEPS = [
  'chromium',
  'chromium_checkout',
  'depot_tools/gclient',
  'recipe_engine/context',
  'recipe_engine/json',
]


def RunSteps(api):
  api.gclient.set_config('chromium')
  api.chromium.set_config('chromium')
  update_result = api.chromium_checkout.ensure_checkout()
  source_dir = update_result.source_root.path
  build_dir = source_dir / 'out/Release'
  with api.context(cwd=api.chromium_checkout.checkout_dir):
    api.chromium.mb_analyze(
        source_dir,
        build_dir,
        api.chromium.get_builder_id(),
        {
            'files': ['base/test/launcher/test_launcher.cc'],
            'test_targets': ['base_unittests'],
            'additional_compile_targets': ['chrome']
        },
    )


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium.try_build(
          builder_group='test_group', builder='test_buildername'),
      api.post_process(post_process.MustRun, 'analyze'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_failure',
      api.chromium.try_build(
          builder_group='test_group', builder='test_buildername'),
      api.step_data(
          'analyze',
          api.json.output({'output': 'ERROR at line 5: missing )'},
                          name="failure_summary"),
          retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.SummaryMarkdown,
          textwrap.dedent('''
          #### Step _analyze_ failed. Error logs are shown below:
          ```
          ERROR at line 5: missing )
          ```
          #### More information can be found in the stdout.
      ''').strip()),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'analyze_failure_no_output',
      api.chromium.try_build(
          builder_group='test_group', builder='test_buildername'),
      api.step_data(
          'analyze',
          api.json.output({'output': ''}, name="failure_summary"),
          retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       "Step('analyze') (retcode: 1)"),
      api.post_process(post_process.DropExpectation),
  )
