# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_tests
from RECIPE_MODULES.depot_tools import bot_update, gclient, tryserver
from RECIPE_MODULES.recipe_engine import assertions, path, properties


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  bot_update: bot_update.API
  chromium: chromium.API
  chromium_tests: chromium_tests.API
  gclient: gclient.API
  path: path.API
  properties: properties.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  properties: properties.TEST_API
  tryserver: tryserver.TEST_API


from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import generators


def RunSteps(api: DEPS):
  api.gclient.set_config('chromium')
  api.chromium.set_config('chromium')

  update_result = api.bot_update.ensure_checkout()

  single_spec = api.properties.get('single_spec')
  test_spec = single_spec if single_spec else {}

  precommit_details = None
  if api.tryserver.is_tryserver:
    precommit_details = generators.PrecommitDetails(
      footers=api.tryserver.get_footers()
    )

  generator = generators.Generator(
    api.chromium_tests,
    update_result.properties,
    update_result.source_root.path,
    precommit_details=precommit_details,
  )
  test_args = generator._get_args_for_test(test_spec)
  if 'expected_args' in api.properties:
    # For some reason, we get expected_args as a tuple instead of a list
    api.assertions.assertEqual(
      list(api.properties.get('expected_args')), test_args
    )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'buildbucket_string',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'args': ['${buildbucket_build_id}'],
        'test': 'base_unittests',
      },
      expected_args=['8945511751514863184'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'buildbucket_unicode',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'args': ['${buildbucket_build_id}'],
        'test': 'base_unittests',
      },
      expected_args=['8945511751514863184'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'buildbucket_dictionary',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'args': ['${buildbucket_build_id}'],
        'test': 'base_unittests',
      },
      expected_args=['8945511751514863184'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'conditional args added',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'conditional_args': [
          {
            'variable': 'buildbucket_project',
            'value': 'chromium',
            'args': ['foo', 'bar'],
          }
        ],
      },
      expected_args=['foo', 'bar'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'conditional args not added',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'conditional_args': [
          {
            'variable': 'buildbucket_project',
            'value': 'chrome',
            'args': ['foo', 'bar'],
          }
        ],
      },
      expected_args=[],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'inverted conditional args added',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'conditional_args': [
          {
            'variable': 'buildbucket_project',
            'value': 'chrome',
            'invert': True,
            'args': ['foo', 'bar'],
          }
        ],
      },
      expected_args=['foo', 'bar'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'inverted conditional args not added',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'conditional_args': [
          {
            'variable': 'buildbucket_project',
            'value': 'chromium',
            'invert': True,
            'args': ['foo', 'bar'],
          }
        ],
      },
      expected_args=[],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'conditional args without variable',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'conditional_args': [{}],
      }
    ),
    api.expect_status('INFRA_FAILURE'),
    api.post_check(
      lambda check, steps: check(
        "Conditional has no 'variable' key"
        in steps['Invalid conditional'].step_text
      )
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'conditional args with unknown variable',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'conditional_args': [
          {
            'variable': 'foobar',
          }
        ],
      }
    ),
    api.expect_status('INFRA_FAILURE'),
    api.post_check(
      lambda check, steps: check(
        "Unknown variable 'foobar'" in steps['Invalid conditional'].step_text
      )
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'use_permissive_angle_pixel_comparison not trybot',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'args': ['${use_permissive_angle_pixel_comparison}'],
        'test': 'base_unittests',
      },
      expected_args=['0'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'use_permissive_angle_pixel_comparison trybot no footer',
    api.chromium.try_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'args': ['${use_permissive_angle_pixel_comparison}'],
        'test': 'base_unittests',
      },
      expected_args=['0'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'use_permissive_angle_pixel_comparison trybot footer not true',
    api.chromium.try_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'args': ['${use_permissive_angle_pixel_comparison}'],
        'test': 'base_unittests',
      },
      expected_args=['0'],
    ),
    api.tryserver.get_footers(
      {'Use-Permissive-Angle-Pixel-Comparison': ['False']},
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'use_permissive_angle_pixel_comparison trybot footer true',
    api.chromium.try_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'args': ['${use_permissive_angle_pixel_comparison}'],
        'test': 'base_unittests',
      },
      expected_args=['1'],
    ),
    api.tryserver.get_footers(
      {'Use-Permissive-Angle-Pixel-Comparison': ['True']},
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'use_permissive_angle_pixel_comparison trybot multiple footers true',
    api.chromium.try_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      single_spec={
        'args': ['${use_permissive_angle_pixel_comparison}'],
        'test': 'base_unittests',
      },
      expected_args=['1'],
    ),
    api.tryserver.get_footers(
      {'Use-Permissive-Angle-Pixel-Comparison': ['foo', 'True']},
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'patch_host public gerrit',
    api.chromium.try_build(
      builder_group='test_group',
      builder='test_buildername',
      git_repo='https://chromium.googlesource.com/chromium/src',
    ),
    api.properties(
      single_spec={
        'args': ['${patch_host}'],
        'test': 'base_unittests',
      },
      expected_args=['gerrit'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'patch_host internal gerrit',
    api.chromium.try_build(
      builder_group='test_group',
      builder='test_buildername',
      git_repo='https://chrome-internal.googlesource.com/chrome/src-internal',
    ),
    api.properties(
      single_spec={
        'args': ['${patch_host}'],
        'test': 'base_unittests',
      },
      expected_args=['gerrit-internal'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'patch_host other gerrit',
    api.chromium.try_build(
      builder_group='test_group',
      builder='test_buildername',
      git_repo='https://other.googlesource.com/other/repo',
    ),
    api.properties(
      single_spec={
        'args': ['${patch_host}'],
        'test': 'base_unittests',
      },
      expected_args=['None'],
    ),
    api.post_process(post_process.DropExpectation),
  )
