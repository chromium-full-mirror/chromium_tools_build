# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import copy

from recipe_engine import recipe_api, step_data

from RECIPE_MODULES.build.chromium_tests.resultdb import ResultDB
from RECIPE_MODULES.depot_tools.gclient import api as gclient

from PB.go.chromium.org.luci.resultdb.proto.v1 import common as common_rdb_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import invocation as invocation_pb


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
      self._checkout_dir = self.m.path.cache_dir.join('builder')
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

  def ensure_checkout(
      self,
      *,
      timeout: int | None = None,
      **kwargs,
  ) -> step_data.StepData:
    """Wrapper for bot_update.ensure_checkout with chromium-specific additions.

    Args:
      timeout: Timeout in seconds for bot_update.ensure_checkout. If the timeout
        value is set on this module's properties, this will be ignored.
      **kwargs: Keyword arguments to forward on to bot_update.ensure_checkout.
        The following arguments have overridden defaults:
        * no_fetch_tags: True
    """
    kwargs.setdefault('no_fetch_tags', True)

    timeout = int(self.timeout) if self.timeout else timeout

    if self.m.reclient.instance:
      self.m.reclient.use_download_remoteexec_cfg_hook(
          self.m.gclient.c.solutions[0])

    gclient_config = self.m.gclient.c
    with self.m.chromium_bootstrap.update_gclient_config(
        gclient_config) as callback:
      self._report_gclient_config(gclient_config)

      with self.m.context(cwd=self.checkout_dir):
        update_step = self.m.bot_update.ensure_checkout(
            gclient_config=gclient_config, timeout=timeout, **kwargs)

      assert update_step.json.output['did_run']
      # HACK(dnj): Remove after 'crbug.com/398105' has landed
      self.m.chromium.set_build_properties(
          update_step.json.output['properties'])

      callback(update_step.json.output['manifest'])

    self.update_rdb_invocation(
        gitiles_commit=self.m.buildbucket.build.output.gitiles_commit)

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

  def update_rdb_invocation(self, gitiles_commit):
    """Update the rdb invocation to include the SourceSpec being used.

    Args:
      gitiles_commit: (buildbucket.GitilesCommit) gitiles commit position that
        has been checked out.
    """
    if not self.m.resultdb.enabled:
      return None

    with self.m.step.nest('set rdb sources'):
      if not all([
          gitiles_commit.host, gitiles_commit.project, gitiles_commit.id,
          gitiles_commit.ref, gitiles_commit.position
      ]):
        step_result = self.m.step.empty('missing gitiles commit info')
        # TODO(sshrimp): Remove excess debug info. The gitiles_commit might not
        # be set if it's not the primary checkout, however,
        # self.m.buildbucket.build.output will still contain an empty object
        # set this  property so we can check for all edge cases
        step_result.presentation.properties[
            'failed_rdb_invocation_update'] = True
        step_result.presentation.logs['gitiles_commit'] = str(gitiles_commit)
        step_result.presentation.logs['gerrit_changes'] = str(
            self.m.buildbucket.build.input.gerrit_changes)
        return

      baseline_id = ResultDB.generate_baseline_id(
          self.m.buildbucket.build.builder.bucket,
          self.m.buildbucket.builder_name)
      self.m.resultdb.update_invocation(
          step_name='update invocation',
          source_spec=invocation_pb.SourceSpec(
              sources=invocation_pb.Sources(
                  gitiles_commit=common_rdb_pb.GitilesCommit(
                      host=gitiles_commit.host,
                      project=gitiles_commit.project,
                      commit_hash=gitiles_commit.id,
                      ref=gitiles_commit.ref,
                      position=gitiles_commit.position,
                  ),
                  changelists=[
                      common_rdb_pb.GerritChange(
                          host=change.host,
                          project=change.project,
                          change=change.change,
                          patchset=change.patchset) for change in
                      self.m.buildbucket.build.input.gerrit_changes
                  ])),
          baseline_id=baseline_id)
