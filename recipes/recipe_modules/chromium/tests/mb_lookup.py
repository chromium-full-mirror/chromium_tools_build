# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_types

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.recipe_engine import (
    assertions,
    path,
    properties,
    raw_io,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  chromium: chromium.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API


def RunSteps(api: DEPS):
  api.chromium.set_config(
      api.properties.get('chromium_config', 'chromium'),
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'),
      TARGET_CROS_BOARDS=api.properties.get('target_cros_boards'))
  source_dir = api.path.cache_dir / 'builder/src'

  gn_args = api.chromium.mb_lookup(
      source_dir,
      chromium_types.BuilderId.create_for_group('test-group', 'test-builder'),
      recursive=api.properties.get('recursive', False),
  )
  expected_gn_args = api.properties.get('expected_gn_args')
  api.assertions.assertEqual(gn_args, expected_gn_args)


def GenTests(api: TEST_DEPS):
  gn_args = '\n'.join((
      'target_cpu = "x86"',
      'target_sysroot = "//build/linux"',
  ))
  expected_step_text = [
      'target_cpu = "x86"',
      'target_sysroot = "//build/linux"',
  ]

  yield api.test(
      'basic',
      api.properties(expected_gn_args=gn_args),
      api.step_data('lookup GN args', stdout=api.raw_io.output_text(gn_args)),
      api.post_process(post_process.StepCommandContains, 'lookup GN args',
                       ['--quiet']),
      api.post_process(post_process.StepTextContains, 'lookup GN args',
                       expected_step_text),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cros_boards',
      api.properties(
          target_platform='chromeos', target_cros_boards='x86-generic'),
      api.properties(expected_gn_args=gn_args),
      api.step_data('lookup GN args', stdout=api.raw_io.output_text(gn_args)),
      api.post_process(post_process.StepCommandContains, 'lookup GN args',
                       ['--quiet']),
      api.post_process(post_process.StepTextContains, 'lookup GN args',
                       expected_step_text),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'recursive',
      api.properties(expected_gn_args=gn_args, recursive=True),
      api.step_data('lookup GN args', stdout=api.raw_io.output_text(gn_args)),
      api.post_process(post_process.StepCommandContains, 'lookup GN args',
                       ['--recursive']),
      api.post_process(post_process.StepTextContains, 'lookup GN args',
                       expected_step_text),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'raise_on_failure',
      api.properties(expected_gn_args=gn_args),
      api.step_data('lookup GN args', retcode=1),
      api.post_process(post_process.StepCommandContains, 'lookup GN args',
                       ['--quiet']),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )
