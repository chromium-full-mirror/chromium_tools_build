# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'squashfs',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/path',
]


def RunSteps(api):
  if 'binary_not_found' not in api.properties:
    api.path.mock_add_paths(
        api.path.start_dir.join('squashfs', 'squashfs-tools', 'mksquashfs'))
  if 'compression_algorithm' in api.properties:
    api.squashfs.mksquashfs('some/folder', 'out.squash',
                            api.properties['compression_algorithm'],
                            api.properties['compression_level'])
  if 'block_size' in api.properties:
    api.squashfs.mksquashfs(
        'some/folder', 'out.squash', block_size=api.properties['block_size'])
  api.squashfs.mksquashfs('some/folder', 'out.squash')


def GenTests(api):
  yield api.test('basic')

  yield api.test(
      'zstd',
      api.properties(compression_algorithm='zstd', compression_level=22),
      api.post_process(post_process.DropExpectation))

  yield api.test('block_size', api.properties(block_size='256K'),
                 api.post_process(post_process.DropExpectation))

  yield api.test('fail_on_windows', api.platform('win', 64),
                 api.expect_status('FAILURE'),
                 api.post_process(post_process.DropExpectation))

  yield api.test('binary_not_found', api.properties(binary_not_found=True,),
                 api.expect_status('FAILURE'),
                 api.post_process(post_process.DropExpectation))
