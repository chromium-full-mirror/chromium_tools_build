# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


from __future__ import annotations

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2
from recipe_engine import recipe_api

COMPILATOR_WATCHER_GIT_REVISION = '27c191f304c8d7329a393d8a69020fc14032c3c3'

BUILD_CANCELED_SUMMARY = 'Build was canceled.'
BUILD_WRONGLY_CANCELED_SUMMARY = (
  'Compilator was canceled before the parent orchestrator was canceled.'
)


class V8OrchestratorApi(recipe_api.RecipeApi):
  INHERIT = object()

  def create_compilator_handler(self, step_suffix=None):
    return ProdCompilatorHandler(self.m, step_suffix=step_suffix)

  def orchestrated_compilation(
    self, compilator_name, initialize_testing=lambda: None
  ):
    """Orchestrate a compilation.

    Args:
      compilator_name: The name of the compilator to trigger.
      initialize_testing: A initialization function that can be run while
      waiting for the compilation to end. Used to initialize testing in V8.
    Returns:
      A tuple of (compilator_properties, result) where compilator_properties is
      the properties of the compilator build and result is a RawResult if the
      build was canceled or None otherwise.
    """
    try:
      compilator_handler = self.create_compilator_handler()

      with self.m.step.nest('initialization'):
        # Start compilator build.
        build = compilator_handler.trigger_compilator(compilator_name)
        initialize_testing()

      # Wait for compilator build to complete and stream steps.
      sub_build = compilator_handler.launch_compilator_watcher(build)

      # This condition should be rare as swarming only propagates
      # cancellations from parent -> child.
      if sub_build.status == common_pb.CANCELED:
        raise self.m.step.InfraFailure(BUILD_WRONGLY_CANCELED_SUMMARY)

      if 'compilator_properties' not in sub_build.output.properties:
        return None, result_pb2.RawResult(
          status=sub_build.status, summary_markdown=sub_build.summary_markdown
        )

      # Initialize the test specs the compilator retrieved from the checkout.
      comp_props = sub_build.output.properties['compilator_properties']
      return comp_props, None
    finally:
      if self.m.runtime.in_global_shutdown:
        # pylint: disable=lost-exception
        # Cancellation can cause all sorts of spurious exceptions.
        return None, result_pb2.RawResult(
          status=common_pb.CANCELED, summary_markdown=BUILD_CANCELED_SUMMARY
        )


class CompilatorHandler:
  def __init__(self, api, step_suffix=None):
    self.api = api
    self.step_suffix = step_suffix

  def _add_suffix(self, name):
    if not self.step_suffix:
      return name
    return f'{name} ({self.step_suffix})'


class ProdCompilatorHandler(CompilatorHandler):
  def trigger_compilator(
    self,
    compilator_name,
    revision=None,
    project=None,
    bucket=None,
    gerrit_changes=V8OrchestratorApi.INHERIT,
    gitiles_commit=V8OrchestratorApi.INHERIT,
  ):
    """Trigger a compilator build via buildbucket."""
    project = project or self.api.buildbucket.INHERIT
    bucket = bucket or self.api.buildbucket.INHERIT
    if gerrit_changes == self.api.v8_orchestrator.INHERIT:
      gerrit_changes = self.api.buildbucket.INHERIT
    if gitiles_commit == self.api.v8_orchestrator.INHERIT:
      gitiles_commit = self.api.buildbucket.INHERIT

    request = self.api.buildbucket.schedule_request(
      builder=compilator_name,
      swarming_parent_run_id=self.api.swarming.task_id,
      tags=self.api.buildbucket.tags(**{'hide-in-gerrit': 'pointless'}),
      properties=dict(revision=revision) if revision else {},
      gitiles_commit=gitiles_commit,
      gerrit_changes=gerrit_changes,
      project=project,
      bucket=bucket,
      as_shadow_if_parent_is_led=True,
    )
    return self.api.buildbucket.schedule(
      [request], step_name=self._add_suffix('trigger compilator')
    )[0]

  def launch_compilator_watcher(self, build_handle):
    """Follow the ongoing compilator build and stream the steps into this
    build.
    """
    cipd_pkg = 'infra/chromium/compilator_watcher/${platform}'
    version = 'git_revision:{}'.format(COMPILATOR_WATCHER_GIT_REVISION)
    compilator_watcher = self.api.cipd.ensure_tool(cipd_pkg, version)
    sub_build = build_pb2.Build()
    sub_build.CopyFrom(build_handle)
    cmd = [
      compilator_watcher,
      '--',
      '-compilator-id',
      build_handle.id,
    ]
    build_url = self.api.buildbucket.build_url(build_id=build_handle.id)
    build_link = f'compilator build: {build_handle.id}'
    try:
      ret = self.api.step.sub_build(
        self._add_suffix('compilator steps'), cmd, sub_build
      )
      ret.presentation.links[build_link] = build_url
      return ret.step.sub_build
    except self.api.step.StepFailure as e:
      ret = self.api.step.active_result
      ret.presentation.links[build_link] = build_url
      sub_build = ret.step.sub_build
      if not sub_build:
        raise self.api.step.InfraFailure('sub_build missing from step') from e
      return sub_build
