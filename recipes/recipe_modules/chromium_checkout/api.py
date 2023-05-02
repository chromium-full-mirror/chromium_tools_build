# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import copy

from recipe_engine import recipe_api

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.depot_tools.gclient import api as gclient


class ChromiumCheckoutApi(recipe_api.RecipeApi):

  def __init__(self, input_properties, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._checkout_dir = None
    self._timeout = input_properties.timeout

  @property
  def timeout(self):
    return self._timeout

  @property
  def checkout_dir(self):
    """Returns directory where checkout can be created."""
    # On LUCI, Buildbucket by default maps a per-builder unique directory in
    # as the 'builder' cache. Builders that are intended to share a cache
    # should have a CacheEntry config like:
    #
    #   caches {
    #     path: "builder"
    #     name: "some common name shared by different builders"
    #   }
    #
    # Which will mount that named cache to exactly the same folder.
    #
    # It's important to maintain the same mounted location because file paths
    # can end up in cached goma keys/objects; mounting the named cache to an
    # alternate location could result in goma cache bloating.
    if not self._checkout_dir:
      self._checkout_dir = self.m.path['cache'].join('builder')
    return self._checkout_dir

  @checkout_dir.setter
  def checkout_dir(self, value):
    self._checkout_dir = value

  # TODO (kimstephanie): Recipes should pass in the src path as an argument to
  # recipe_modules instead of recipe_modules using src_dir and checkout_dir to
  # construct these paths.
  @property
  def src_dir(self):
    """Returns the path to the src checkout directory."""
    return self.checkout_dir.join('src')

  def get_files_affected_by_patch(self, relative_to='src/', cwd=None,
                                  report_via_property=False):
    """Returns list of POSIX paths of files affected by patch for "analyze".

    Paths are relative to `relative_to` which for analyze should be 'src/'.
    Paths always use '/' as a path delimiter, no matter the OS.
    """
    if not self.m.tryserver.gerrit_change:
      # There is no patch to begin with.
      return []
    patch_root = self.m.gclient.get_gerrit_patch_root()
    assert patch_root, (
        'local path is not configured for %s' %
            self.m.tryserver.gerrit_change_repo_url)
    cwd = cwd or self.checkout_dir.join(patch_root)
    with self.m.context(cwd=cwd):
      files = self.m.tryserver.get_files_affected_by_patch(
          patch_root,
          report_files_via_property=(
             'affected_files' if report_via_property else None
          ),
      )
    return self.format_affected_file_paths(files, relative_to=relative_to)

  def format_affected_file_paths(self, files, relative_to='src/'):
    for i, path in enumerate(files):
      path = str(path)
      files[i] = self.m.path.relpath(path, relative_to)
      # self.m.path.relpath uses '\' if the machine running the recipe is a
      # windows machine. The results of this function are expected to have a '/'
      # delimiter no matter the OS. Fix this here.
      files[i] = files[i].replace('\\', '/')
    return files

  def ensure_checkout(self, bot_config=None, timeout=None, **kwargs):
    """Wrapper for bot_update.ensure_checkout with chromium-specific additions.

    Args:
      timeout: (seconds) for tiemout of bot_update.ensure_checkout.
    """
    kwargs.setdefault('no_fetch_tags', True)
    bot_config = bot_config or chromium.BuilderSpec.create()

    if self.m.platform.is_win:
      self.m.chromium.taskkill()

    timeout = int(self.timeout) if self.timeout else timeout

    gclient_config = self.m.gclient.c
    with self.m.chromium_bootstrap.update_gclient_config(
        gclient_config) as callback:
      self._report_gclient_config(gclient_config)

      with self.m.context(cwd=self.checkout_dir):
        update_step = self.m.bot_update.ensure_checkout(
            gclient_config=gclient_config,
            clobber=bot_config.clobber,
            timeout=timeout,
            **kwargs)

      assert update_step.json.output['did_run']
      # HACK(dnj): Remove after 'crbug.com/398105' has landed
      self.m.chromium.set_build_properties(
          update_step.json.output['properties'])

      callback(update_step.json.output['manifest'])

    return update_step

  def _report_gclient_config(self, gclient_config):
    # We may need to update revision values to replace revision resolvers with
    # something that is json serializable
    gclient_config = copy.deepcopy(gclient_config)

    def _replace_revision_resolver(revision):
      if isinstance(revision, gclient.RevisionResolver):
        return "*{}*".format(type(revision).__name__)
      return revision

    gclient_config.revisions = {
        k: _replace_revision_resolver(v)
        for k, v in gclient_config.revisions.items()
    }

    for s in gclient_config.solutions:
      s.revision = _replace_revision_resolver(s.revision)

    step = self.m.step('gclient config', [])
    step.presentation.logs['config'] = self.m.json.dumps(
        gclient_config.as_jsonish(include_hidden=True), indent=2).split('\n')
