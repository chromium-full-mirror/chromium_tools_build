# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import (
  DropExpectation,
  StepCommandContains,
  StepCommandDoesNotContain,
)
from RECIPE_MODULES.build.chromium_tests.steps import ResultDB

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.recipe_engine import (
  json,
  path,
  platform,
  properties,
  runtime,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  runtime: runtime.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  api.chromium.set_config(
    api.properties.get('chromium_config', 'chromium'),
    TARGET_PLATFORM=api.properties.get('target_platform', 'linux'),
  )

  for config in api.properties.get('chromium_apply_config', []):
    api.chromium.apply_config(config)

  checkout_dir = api.path.cache_dir / 'builder'
  build_dir = api.chromium.default_build_dir(checkout_dir)

  kwargs = {}
  if api.properties.get('parse_gtest_output'):
    kwargs.update(
      {
        'parse_gtest_output': True,
        'test_launcher_summary_output': api.json.output(),
      }
    )

  if api.properties.get('resultdb'):
    kwargs['resultdb'] = ResultDB.create(enable=True)

  api.chromium.runtest(
    checkout_dir,
    build_dir,
    'base_unittests',
    builder_group=api.properties.get('builder_group'),
    python_mode=api.properties.get('python_mode', False),
    test_type='base_unittests',
    **kwargs,
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.properties(
      buildername='test_buildername', buildnumber=123, bot_id='test_bot_id'
    ),
    api.post_process(
      StepCommandContains,
      'base_unittests',
      [
        'python3',
        'RECIPE_REPO[build]/recipes/runtest.py',
        '--build-dir',
        '[CACHE]/builder/out/4dd2-test_buildernam',
        '--no-xvfb',
        '--test-type=base_unittests',
        '--builder-name=test_buildername',
        '--build-number=123',
        'base_unittests',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'resultdb',
    api.properties(
      buildername='test_buildername',
      buildnumber=123,
      bot_id='test_bot_id',
      resultdb=True,
    ),
    api.chromium.ci_build(
      builder_group='chromium.linux',
      builder='Linux Tests',
    ),
    api.post_process(
      StepCommandContains,
      'base_unittests',
      [
        'rdb',
        'stream',
        '-coerce-negative-duration',
        '-exonerate-unexpected-pass',
        '-inherit-sources',
        '-baseline-id',
        'ci:Linux Tests',
        '--',
        'python3',
        'RECIPE_REPO[build]/recipes/runtest.py',
      ],
    ),
    api.post_process(DropExpectation),
  )

  # In order to get coverage of the LUCI-specific code in runtest.
  yield api.test(
    'android',
    api.properties(
      buildername='test_buildername',
      buildnumber=123,
      bot_id='test_bot_id',
      target_platform='android',
    ),
    api.post_process(
      StepCommandContains,
      'base_unittests',
      [
        '--test-platform',
        'android',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'win',
    api.platform('win', 64),
    api.properties(
      buildername='test_buildername',
      buildnumber=123,
      bot_id='test_bot_id',
      target_platform='win',
    ),
    api.post_process(
      StepCommandContains,
      'base_unittests',
      ['RECIPE_REPO[build]\\recipes\\runtest.py'],
    ),
    api.post_process(
      StepCommandDoesNotContain,
      'base_unittests',
      [
        '--no-xvfb',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'builder_group',
    api.properties(
      buildername='test_buildername',
      buildnumber=123,
      bot_id='test_bot_id',
      builder_group='fake-builder-group',
    ),
    api.post_process(
      StepCommandContains,
      'base_unittests',
      [
        '--builder-group=fake-builder-group',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'parse_gtest_output',
    api.properties(
      buildername='test_buildername',
      buildnumber=123,
      bot_id='test_bot_id',
      parse_gtest_output=True,
    ),
    api.post_process(
      StepCommandContains,
      'base_unittests',
      [
        '--parse-gtest-output',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'python_mode',
    api.properties(
      buildername='test_buildername',
      buildnumber=123,
      bot_id='test_bot_id',
      python_mode=True,
    ),
    api.post_process(
      StepCommandContains,
      'base_unittests',
      [
        '--run-python-script',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'tsan',
    api.properties(
      buildername='test_buildername',
      buildnumber=123,
      bot_id='test_bot_id',
      chromium_config='chromium_clang',
      chromium_apply_config=['tsan2'],
    ),
    api.post_process(
      StepCommandContains,
      'base_unittests',
      [
        '--enable-tsan',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'msan',
    api.properties(
      buildername='test_buildername',
      buildnumber=123,
      bot_id='test_bot_id',
      chromium_config='chromium_msan',
    ),
    api.post_process(
      StepCommandContains,
      'base_unittests',
      [
        '--enable-msan',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'lsan',
    api.properties(
      buildername='test_buildername',
      buildnumber=123,
      bot_id='test_bot_id',
      chromium_config='chromium_clang',
      chromium_apply_config=['lsan'],
    ),
    api.post_process(
      StepCommandContains,
      'base_unittests',
      [
        '--enable-lsan',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'asan',
    api.properties(
      buildername='test_buildername',
      buildnumber=123,
      bot_id='test_bot_id',
      chromium_apply_config=['chromium_win_asan'],
    ),
    api.post_process(
      StepCommandContains,
      'base_unittests',
      [
        '--enable-asan',
      ],
    ),
    api.post_process(DropExpectation),
  )
