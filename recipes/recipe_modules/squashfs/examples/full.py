# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import squashfs
from RECIPE_MODULES.recipe_engine import path, platform, properties


@dataclass
class DEPS(RecipeScriptApi):
  path: path.API
  platform: platform.API
  properties: properties.API
  squashfs: squashfs.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  if 'binary_not_found' not in api.properties:
    api.path.mock_add_paths(
      api.path.start_dir.joinpath('squashfs', 'squashfs-tools', 'mksquashfs')
    )
  if 'compression_algorithm' in api.properties:
    api.squashfs.mksquashfs(
      'some/folder',
      'out.squash',
      api.properties['compression_algorithm'],
      api.properties['compression_level'],
    )
  if 'block_size' in api.properties:
    api.squashfs.mksquashfs(
      'some/folder', 'out.squash', block_size=api.properties['block_size']
    )
  api.squashfs.mksquashfs('some/folder', 'out.squash')


def GenTests(api: TEST_DEPS):
  yield api.test('basic')

  yield api.test(
    'zstd',
    api.properties(compression_algorithm='zstd', compression_level=22),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'block_size',
    api.properties(block_size='256K'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'fail_on_windows',
    api.platform('win', 64),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'binary_not_found',
    api.properties(
      binary_not_found=True,
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
