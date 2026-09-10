# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
import textwrap

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_checkout
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.recipe_engine import context, json


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  context: context.API
  gclient: gclient.API
  json: json.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  json: json.TEST_API


def RunSteps(api: DEPS):
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
        'additional_compile_targets': ['chrome'],
      },
    )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.chromium.try_build(
      builder_group='test_group', builder='test_buildername'
    ),
    api.post_process(post_process.MustRun, 'analyze'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'analyze_failure',
    api.chromium.try_build(
      builder_group='test_group', builder='test_buildername'
    ),
    api.step_data(
      'analyze',
      api.json.output(
        {'output': 'ERROR at line 5: missing )'}, name="failure_summary"
      ),
      retcode=1,
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.SummaryMarkdown,
      textwrap.dedent('''
          #### Step _analyze_ failed. Error logs are shown below:
          ```
          ERROR at line 5: missing )
          ```
          #### More information can be found in the stdout.
      ''').strip(),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'analyze_failure_no_output',
    api.chromium.try_build(
      builder_group='test_group', builder='test_buildername'
    ),
    api.step_data(
      'analyze',
      api.json.output({'output': ''}, name="failure_summary"),
      retcode=1,
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.SummaryMarkdown, "Step('analyze') (retcode: 1)"
    ),
    api.post_process(post_process.DropExpectation),
  )
