# Copyright (c) 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api


class SquashfsApi(recipe_api.RecipeApi):
  def _get_squashfs_path(self):
    squashfs_dir = self.m.path.start_dir / 'squashfs'
    ensure_file = self.m.cipd.EnsureFile().add_package(
      'infra/3pp/tools/squashfs/linux-amd64',
      '97pLXFMaDo0YFKrWyL_wfrZHyTNXM9iO6T_uRHkMkrQC',
    )
    self.m.cipd.ensure(squashfs_dir, ensure_file)
    return squashfs_dir

  def mksquashfs(
    self,
    folder_path,
    output_file_path,
    compression_algorithm=None,
    compression_level=None,
    block_size=None,
  ):
    """Archive a folder to a file using squashfs format.

    Args:
      folder_path: String representing the folder to compress.
      output_file_path: String representing the output image.
      compression_algorithm: String the algorithm squashfs will use.
      compression_level: int the compression level

    Example: mksquashfs('/abs/path/', '/abs/another/path/image.sqsh')
    Meaning call:
    mksquashfs /abs/path/ /abs/another/path/image.sqsh
    See more at:
    https://github.com/plougher/squashfs-tools/blob/HEAD/USAGE
    """
    if not self.m.platform.is_linux:
      self.m.step.empty(
        'Mksquashfs only supports linux',
        status=self.m.step.FAILURE,
        step_text='Only use this for linux builds.',
      )

    squashfs_dir = self._get_squashfs_path()
    binary_path = squashfs_dir.joinpath('squashfs-tools', 'mksquashfs')
    if not self.m.path.exists(binary_path):
      self.m.step.empty(
        'Mksquashfs is not found',
        status=self.m.step.FAILURE,
        step_text='Binary not found at %s.' % binary_path,
      )

    cmd = [
      'python3',
      self.resource('squashfs_invoke.py'),
      '--binary-path',
      self.m.path.abspath(binary_path),
      '--folder',
      folder_path,
      '--output-file',
      output_file_path,
    ]
    if compression_algorithm:
      cmd.extend(['--compression-algorithm', compression_algorithm])
    if compression_level:
      cmd.extend(['--compression-level', str(compression_level)])
    if block_size:
      cmd.extend(['--block-size', block_size])
    self.m.step('mksquashfs', cmd)
