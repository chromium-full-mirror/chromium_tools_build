# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Common steps for recipes that sync/build Cronet sources."""

import sys

from recipe_engine import recipe_api
from recipe_engine.engine_types import freeze
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build import chromium


class CronetApi(recipe_api.RecipeApi):

  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self._repo_path = None

  DASHBOARD_UPLOAD_URL = 'https://chromeperf.appspot.com'

  def init_and_sync(self, recipe_config, kwargs, chromium_apply_config=None):
    default_kwargs = {
        'REPO_URL': 'https://chromium.googlesource.com/chromium/src',
        'INTERNAL': False,
        'REPO_NAME': 'src',
        'BUILD_CONFIG': 'Debug'
    }
    droid = self.m.chromium_android
    default_kwargs.update(kwargs)
    droid.configure_from_properties(recipe_config, **default_kwargs)
    droid.apply_config('use_devil_provision')
    self.m.chromium.apply_config('cronet_builder')
    for c in chromium_apply_config or []:
      self.m.chromium.apply_config(c)
    droid.init_and_sync(use_bot_update=True)

  def build(self,
            builder_id=None,
            targets=None,
            use_reclient=True):
    builder_id = builder_id or self.m.chromium.get_builder_id()
    self.m.chromium.runhooks()
    if self.m.chromium.c.project_generator.tool == 'gn':  # pragma: no cover
      assert (self.m.chromium.c.HOST_PLATFORM == 'linux' and
              self.m.chromium.c.HOST_BITS == 64)
      gn_path = self.m.path['checkout'].join('buildtools', 'linux64', 'gn')
      if not self.m.path.exists(gn_path):
        gn_path = self.m.path['checkout'].join('third_party', 'gn', 'gn')
      self.m.chromium.run_gn(use_reclient=use_reclient, gn_path=gn_path)
    elif self.m.chromium.c.project_generator.tool == 'mb':
      self.m.chromium.mb_gen(builder_id, use_reclient=use_reclient)
    return self.m.chromium.compile(targets=targets, use_reclient=use_reclient)

  def get_version(self):
    version = self.m.chromium.get_version()
    return "%s.%s.%s.%s" % (version['MAJOR'], version['MINOR'],
                            version['BUILD'], version['PATCH'])

  def get_bucket(self, platform):
    if self.m.runtime.is_experimental:
      return 'chromium-cronet/experimental/%s' % platform
    return 'chromium-cronet/%s' % platform

  def get_default_cronet_dir(self):
    return self.m.path['checkout'].join('out',
                                        self.m.chromium_android.c.BUILD_CONFIG,
                                        'cronet')

  def generate_changelist(self, cronetdir=None):
    cronetdir = cronetdir or self.get_default_cronet_dir()
    cmd = [
        'python3',
        self.resource('generate_changelist.py'), "--git_dir",
        self.m.path['checkout'], "--output_file",
        cronetdir.join('CHANGELIST')
    ]
    return self.m.step('generate changelist file', cmd)

  def upload_package(self, build_config, cronetdir=None, platform='android'):
    cronetdir = cronetdir or self.get_default_cronet_dir()
    destdir = self.get_version() + '/' + build_config
    # Upload cronet version first to ensure that destdir is created.
    self.m.gsutil.upload(
        source=cronetdir.join('VERSION'),
        bucket=self.get_bucket(platform),
        dest=destdir + '/VERSION',
        name='upload_cronet_version',
        link_name='Cronet version')
    self.m.gsutil.upload(
        source=cronetdir,
        bucket=self.get_bucket(platform),
        dest=destdir,
        args=['-R'],
        name='upload_cronet_package',
        link_name='Cronet package')

  def run_perf_tests(self,
                     perf_builder_name_alias,
                     use_reclient=True):
    # TODO(crbug.com/1324274): Revive, or bury the perf test bot for good
    raise NotImplementedError(
        "The perf bot is broken. See crbug.com/1324274 for details.")
