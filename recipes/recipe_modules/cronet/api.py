# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Common steps for recipes that sync/build Cronet sources."""

from __future__ import annotations

from recipe_engine import recipe_api
from recipe_engine.config_types import Path


class CronetApi(recipe_api.RecipeApi):
  def build(self, source_dir: Path, *, targets=None):
    builder_id = self.m.chromium.get_builder_id()
    build_dir = self.m.chromium.default_build_dir(source_dir)
    self.m.chromium.runhooks(source_dir, build_dir)
    if self.m.chromium.c.project_generator.tool == 'gn':  # pragma: no cover
      assert (
        self.m.chromium.c.HOST_PLATFORM == 'linux'
        and self.m.chromium.c.HOST_BITS == 64
      )
      gn_path = source_dir / 'buildtools/linux64/gn'
      if not self.m.path.exists(gn_path):
        gn_path = source_dir / 'third_party/gn/gn'
      self.m.chromium.run_gn(source_dir, build_dir, gn_path=gn_path)
    elif self.m.chromium.c.project_generator.tool == 'mb':
      self.m.chromium.mb_gen(source_dir, build_dir, builder_id)
    return self.m.chromium.compile(source_dir, build_dir, targets=targets)

  def _get_version(self, source_dir: Path) -> str:
    version = self.m.chromium.get_version(source_dir)
    return "%s.%s.%s.%s" % (
      version['MAJOR'],
      version['MINOR'],
      version['BUILD'],
      version['PATCH'],
    )

  def _bucket(self) -> str:
    if self.m.runtime.is_experimental:
      return 'chromium-cronet/experimental/android'
    return 'chromium-cronet/android'

  def _cronet_dir(self, source_dir: Path) -> Path:
    return self.m.chromium.default_build_dir(source_dir) / 'cronet'

  def generate_changelist(self, source_dir: Path):
    cronet_dir = self._cronet_dir(source_dir)
    cmd = [
      'python3',
      self.resource('generate_changelist.py'),
      "--git_dir",
      source_dir,
      "--output_file",
      cronet_dir / 'CHANGELIST',
    ]
    return self.m.step('generate changelist file', cmd)

  def upload_package(self, source_dir: Path, build_config: str):
    cronet_dir = self._cronet_dir(source_dir)
    dest_dir = self._get_version(source_dir) + '/' + build_config
    bucket = self._bucket()
    # Upload cronet version first to ensure that destdir is created.
    self.m.gsutil.upload(
      source=cronet_dir / 'VERSION',
      bucket=bucket,
      dest=dest_dir + '/VERSION',
      name='upload_cronet_version',
      link_name='Cronet version',
    )
    self.m.gsutil.upload(
      source=cronet_dir,
      bucket=bucket,
      dest=dest_dir,
      args=['-R'],
      name='upload_cronet_package',
      link_name='Cronet package',
    )

  def run_perf_tests(self, perf_builder_name_alias):
    # TODO(crbug.com/1324274): Revive, or bury the perf test bot for good
    raise NotImplementedError(
      "The perf bot is broken. See crbug.com/1324274 for details."
    )
