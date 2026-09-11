# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from PB.recipe_modules.build.chromium_toolchain import (
  properties as properties_pb,
)
from recipe_engine import recipe_api


class ChromiumToolchainApi(recipe_api.RecipeApi):
  """API for chromium toolchain recipes."""

  InputProperties = properties_pb.InputProperties
  ToolchainType = properties_pb.InputProperties.ToolchainType

  def __init__(self, properties: properties_pb.InputProperties, **kwargs):
    super().__init__(**kwargs)
    self._properties = properties

  def trusted_package(
    self,
    properties: properties_pb.InputProperties | None = None,
  ) -> None:
    properties = properties or self._properties
    if properties.toolchain == properties_pb.InputProperties.UNKNOWN:
      raise self.m.step.StepFailure(
        'toolchain property must be set to CLANG or RUST'
      )

    if properties.toolchain == properties_pb.InputProperties.CLANG:
      toolchain_name = 'clang'
    else:
      toolchain_name = 'rust'

    # Run TBI packaging
    with self.m.step.nest(f'package {toolchain_name}'):
      tbi_client = self.m.cipd.ensure_tool(
        'infra_internal/tools/security/lexan_tbi_client/${platform}',
        'latest',
      )

      args = [
        tbi_client,
        '--result_file',
        self.m.json.output(name='summary'),
      ]

      if properties.toolchain == properties_pb.InputProperties.CLANG:
        args.append('--clang')
      if properties.config_path:
        args.extend(['--config', properties.config_path])

      if properties.trusted_build_instance:
        args.extend(['--instance', properties.trusted_build_instance])
      if properties.trusted_build_instance_pool:
        args.extend(['--pool', properties.trusted_build_instance_pool])
      if properties.trusted_build_instance_env:
        args.extend(['--env', properties.trusted_build_instance_env])
      if properties.trusted_build_instance_project:
        args.extend(['--project', properties.trusted_build_instance_project])

      if self.m.buildbucket.build.id:
        args.extend(['--build_prefix', f'bb-{self.m.buildbucket.build.id}'])

      # TODO(dlf): Support rust

      # If we are in a CI build (not a try job), override the source commit.
      # For try jobs, lexan_tbi_client automatically detects Gerrit env vars.
      changes = self.m.buildbucket.build.input.gerrit_changes
      if not changes:
        gitiles_commit = self.m.buildbucket.gitiles_commit
        if gitiles_commit and gitiles_commit.id:
          args.extend(['-chromium_src_commit', gitiles_commit.id])

      env = {}
      if changes:
        change = changes[0]
        env = {
          'GERRIT_HOST': str(change.host),
          'GERRIT_PROJECT': str(change.project),
          'GERRIT_CHANGE_ID': str(change.change),
          'GERRIT_PATCHSET_ID': str(change.patchset),
        }

      # Run the TBI client.
      with self.m.context(env=env):
        res = self.m.step('request build', args)
        if res.exc_result.retcode == 0:
          res.presentation.step_text = 'TBI finished successfully'
