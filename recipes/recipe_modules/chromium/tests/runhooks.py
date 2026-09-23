# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.recipe_engine import (
  path,
  platform,
  properties,
  runtime,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  path: path.API
  platform: platform.API
  properties: properties.API
  runtime: runtime.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  api.chromium.set_config(
    'chromium',
    TARGET_PLATFORM=api.properties.get('target_platform', 'linux'),
    TARGET_CROS_BOARDS=api.properties.get('target_cros_boards'),
  )
  if api.properties.get('clobber'):
    api.chromium.apply_config('clobber')
  api.chromium.apply_config('mb')

  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = source_dir / 'out/Release'

  api.chromium.runhooks(source_dir, build_dir=build_dir)


def GenTests(api: TEST_DEPS):

  yield api.test(
    'basic',
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'clobber',
    api.properties(clobber=True),
    api.post_process(post_process.StepSuccess, 'clobber'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'mac',
    api.platform.name('mac'),
    api.properties(target_platform='mac'),
    api.post_process(post_process.StepSuccess, 'ensure_installed'),
    api.post_process(
      post_process.StepCommandContains,
      'ensure_installed',
      [
        'infra/tools/mac_toolchain/${platform} '
        'git_revision:ed58ac9443fe1754e84d596bdaf9961cbaa6e85b'
      ],
    ),
    api.post_process(
      post_process.StepEnvContains,
      'gclient runhooks',
      {'FORCE_MAC_TOOLCHAIN': '1'},
    ),
    api.post_process(
      post_process.StepEnvContains,
      'gclient runhooks',
      {'GYP_DEFINES': 'clang=1'},
    ),
    api.post_process(
      post_process.StepEnvContains,
      'gclient runhooks',
      {'MAC_TOOLCHAIN_INSTALLER': '[START_DIR]/mac_toolchain'},
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'with_mac_toolchain_version',
    api.platform.name('mac'),
    api.properties(target_platform='mac'),
    api.chromium.properties(mac_toolchain_version='custom_version'),
    api.post_process(post_process.StepSuccess, 'ensure_installed'),
    api.post_process(
      post_process.StepCommandContains,
      'ensure_installed',
      ['infra/tools/mac_toolchain/${platform} git_revision:custom_version'],
    ),
    api.post_process(post_process.DropExpectation),
  )
