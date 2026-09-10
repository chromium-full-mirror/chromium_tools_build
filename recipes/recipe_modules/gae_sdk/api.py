# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api


class GaeSdkApi(recipe_api.RecipeApi):
  PLAT_PYTHON = 'python'
  PLAT_GO = 'go'

  # Map of {Platform => {Arch => (base, dirname)}}. Platform names are
  # "recipe_engine/platform" values.
  _PKG_MAP = {
    PLAT_PYTHON: {
      'all': ('google_appengine_', 'google_appengine'),
    },
    PLAT_GO: {
      'linux-amd64': ('go_appengine_sdk_linux_amd64-', 'go_appengine'),
      'mac-amd64': ('go_appengine_sdk_darwin_amd64-', 'go_appengine'),
    },
  }

  # Map of architecture bitness to CIPD bitness suffix.
  _BITS_MAP = {
    64: 'amd64',
  }

  class PackageNotFound(Exception):
    def __init__(self, plat, arch):
      super().__init__('Package not found for %s on %s' % (plat, arch))
      self.plat = plat
      self.arch = arch

  @property
  def latest_ref(self):
    return 'latest'

  def package_spec(self, plat, arch):
    plat_dict = self._PKG_MAP.get(plat, {})
    for a in (arch, 'all'):
      spec = plat_dict.get(a)
      if spec:
        download_base, dirname = spec
        pkg_name = 'infra/gae_sdk/%s/%s' % (plat, a)
        return pkg_name, download_base, dirname
    raise self.PackageNotFound(plat, arch)

  def package(self, plat, arch=None):
    arch = arch or '%s-%s' % (
      self.m.platform.name,
      self._BITS_MAP[self.m.platform.bits],
    )
    pkg_name, _, _ = self.package_spec(plat, arch)
    return pkg_name

  @property
  def platforms(self):
    return sorted(self._PKG_MAP)

  @property
  def all_packages(self):
    for plat, arch_dict in sorted(self._PKG_MAP.items()):
      for arch in sorted(arch_dict):
        yield plat, arch

  def fetch(self, plat, dst):
    """Fetch the AppEngine SDK for the specified platform.

    Args:
      plat (str): platform string, one of the PLAT_ local variables.
      dst (path.Path): The destination directory to extract it.
    """
    self.m.cipd.ensure(
      dst,
      self.m.cipd.EnsureFile().add_package(self.package(plat), self.latest_ref),
    )
