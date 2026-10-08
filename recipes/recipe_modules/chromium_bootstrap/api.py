# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import contextlib

from google.protobuf import json_format
from recipe_engine import recipe_api


class ChromiumBootstrapApi(recipe_api.RecipeApi):
  """Recipe module for modifying config with bootstrapped information.

  The bootstrapper communicates information about the bootstrapping
  process so that the recipe can operate in a manner that is consistent
  with the bootstrapping process (e.g. the bootstrapper communicates the
  gitiles commits accessed so that the recipe can sync code to the same
  version that the properties came from).
  """

  def __init__(self, input_properties, **kwargs):
    super().__init__(**kwargs)
    self._commits = tuple(input_properties.commits)
    self._skip_analysis_reasons = tuple(input_properties.skip_analysis_reasons)
    self._exe = input_properties.exe

  def initialize(self):
    # TODO(gbeaty) Once we have the ability to have Milo display the
    # bootstrapped properties, this can be removed
    if '$build/chromium_bootstrap' in self.m.properties:
      bucket = self.m.buildbucket.build.builder.bucket
      assert not (bucket == 'official' or bucket.startswith('official.')), (
        'Official builders cannot be bootstrapped'
      )

      result = self.m.step('bootstrapped properties', [])
      result.presentation.step_text = (
        'This build was bootstrapped, see properties log for actual properties'
      )
      result.presentation.logs['properties'] = self.m.json.dumps(
        dict(self.m.properties), indent=2
      )

  @property
  def skip_analysis_reasons(self):
    """Reasons to skip analysis as determined by the bootstrapper."""
    return self._skip_analysis_reasons

  @property
  def exe(self):
    """Information about the executable being bootstrapped

    For led builds testing recipe changes (like with `led edit-recipe-bundle`
    or `led edit-payload`), the exe contains a CASReference instead of cipd
    information.
    """
    return self._exe

  def update_trigger_properties(self, props):
    """Update the properties used when triggering another builder.

    This will ensure that the triggered builder will use be bootstrapped
    in a manner that is consistent with the triggering builder:
    * Properties will be read from the same revision as the triggering
      builder (if the trigger specifies a gitiles commit for the config
      repo, it will be used instead).
    """
    bootstrap_trigger_props = {}
    if self._commits:
      bootstrap_trigger_props['commit'] = self._commits
    if bootstrap_trigger_props:
      props.update(
        {
          '$bootstrap/trigger': {
            'commits': [json_format.MessageToDict(c) for c in self._commits],
          },
        }
      )

  @contextlib.contextmanager
  def update_gclient_config(self, gclient_config=None):
    """Update the gclient config to be consistent with the bootstrapper.

    Upon entering the context controlled by this context manager, the
    gclient config's revision map will be updated so that for any repos
    that were accessed by the bootstrapper, the same version should be
    checked out.

    The context manager MUST be bound to a target in the with statement,
    with the bound value being a callback that MUST be called with the
    bot_update manifest before exiting the context normally (the
    callback need not be called in the event of an exception). The
    callback will raise an InfraFailure if one of the repos that was
    checked out by the bootstrapper was also checked out by bot_update
    and there was no entry for the repo in repo_path_map. In such a
    case, there will not have been a way to update the gclient config to
    check out the appropriate revision of the repo.
    """
    gclient_config = gclient_config or self.m.gclient.c
    missing_repos = []
    for commit in self._commits:
      repo = 'https://{}/{}'.format(commit.host, commit.project)
      if repo not in gclient_config.repo_path_map:
        missing_repos.append(repo)
        continue
      path, _ = gclient_config.repo_path_map[repo]
      gclient_config.revisions[path] = commit.id or commit.ref

    not_called = object()
    manifest_holder = [not_called]

    def callback(manifest):
      manifest_holder[0] = manifest

    yield callback

    manifest = manifest_holder[0]
    if manifest is not_called:
      raise recipe_api.InfraFailure(
        'The callback from update_gclient_config'
        ' must be called with the manifest from bot_update'
      )

    if missing_repos:
      checked_out_repos = set()
      for revision in manifest.values():  # pylint: disable=no-member
        repo = revision['repository']
        if repo.endswith('.git'):
          repo = repo[: -len('.git')]
        checked_out_repos.add(repo)

      for repo in missing_repos:
        if repo in checked_out_repos:
          raise recipe_api.InfraFailure(
            f'The bootstrapper and bot_update both checked out {repo},'
            f' but {repo}'
            " does not appear in the gclient config's repo_path_map"
          )
