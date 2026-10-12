# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import datetime

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
    step_name: str | None = None,
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
    with self.m.step.nest(step_name or f'package {toolchain_name}'):
      tbi_client = self.m.cipd.ensure_tool(
        'infra_internal/tools/security/lexan_tbi_client/${platform}',
        'latest',
      )

      generic_args = []
      if properties.trusted_build_instance:
        generic_args.extend(['--instance', properties.trusted_build_instance])
      if properties.trusted_build_instance_pool:
        generic_args.extend(['--pool', properties.trusted_build_instance_pool])
      if properties.trusted_build_instance_env:
        generic_args.extend(['--env', properties.trusted_build_instance_env])
      if properties.trusted_build_instance_project:
        generic_args.extend(
          ['--project', properties.trusted_build_instance_project]
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

      args.extend(generic_args)

      if self.m.buildbucket.build.id:
        args.extend(['--build_prefix', f'bb-{self.m.buildbucket.build.id}'])

      build_timeout = self.m.buildbucket.build.execution_timeout.seconds
      if build_timeout > 0:
        args.extend(['--build_timeout', f'{build_timeout}s'])

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
        res = self.m.step(
          'request build',
          args,
          step_test_data=lambda: self.m.json.test_api.output(
            data={
              'buildName': (
                'projects/fake-project/locations/fake-location/'
                'instances/fake-instance/pools/fake-pool/builds/fake-build'
              ),
            },
            name='summary',
          ),
        )
        if res.exc_result.retcode == 0:
          res.presentation.step_text = 'TBI finished successfully'

      build_name = None
      summary = res.json.outputs.get('summary') or res.json.output
      if summary and isinstance(summary, dict):
        build_name = summary.get('buildName')

      if build_name:
        fetch_args = [
          tbi_client,
          '--build_name',
          build_name,
          '--logs=false',
        ] + generic_args

        fetch_res = self.m.step(
          'fetch build',
          fetch_args,
          stdout=self.m.json.output(),
          step_test_data=lambda: self.m.json.test_api.output_stream(
            {
              'timestamps': {
                'createdTime': '2026-10-11T19:00:34.630447Z',
                'startedTime': '2026-10-11T19:41:23.110447Z',
                'finishedTime': '2026-10-11T22:10:00.105871Z',
                'lastModifiedTime': '2026-10-11T22:10:00.105871Z',
              },
            }
          ),
        )
        build_details = fetch_res.stdout
        if build_details and isinstance(build_details, dict):
          timestamps = build_details.get('timestamps')
          if timestamps and isinstance(timestamps, dict):
            created_str = timestamps.get('createdTime')
            started_str = timestamps.get('startedTime')
            if created_str and started_str:
              created_time = datetime.datetime.fromisoformat(
                created_str.replace('Z', '+00:00')
              )
              started_time = datetime.datetime.fromisoformat(
                started_str.replace('Z', '+00:00')
              )
              ttfs = round((started_time - created_time).total_seconds(), 6)
              fetch_res.presentation.properties['time-to-first-tbi-step'] = ttfs
              fetch_res.presentation.step_text = f'TTFS: {ttfs}s'
