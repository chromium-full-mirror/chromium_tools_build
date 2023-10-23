# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from recipe_engine import recipe_api
from google.protobuf import json_format

class V8OrchestratorApi(recipe_api.RecipeApi):

  INHERIT = object()

  def create_compilator_handler(self, enable_led=True, step_suffix=None):
    # TODO: enable led when we trigger real compilators
    if enable_led and self.m.led.launched_by_led:
      return LedCompilatorHandler(self.m, step_suffix=step_suffix)
    return ProdCompilatorHandler(self.m, step_suffix=step_suffix)


class CompilatorHandler:
  def __init__(self, api, step_suffix=None):
    self.api = api
    self.step_suffix = step_suffix

  def _add_suffix(self, name):
    if not self.step_suffix:
      return name
    return f'{name} ({self.step_suffix})'


class ProdCompilatorHandler(CompilatorHandler):
  def trigger_compilator(self,
      compilator_name,
      revision=None,
      bucket=None,
      gerrit_changes=V8OrchestratorApi.INHERIT,
  ):
    """Trigger a compilator build via buildbucket."""
    bucket = bucket or self.api.buildbucket.INHERIT
    if gerrit_changes == self.api.v8_orchestrator.INHERIT:
      gerrit_changes = self.api.buildbucket.INHERIT

    request = self.api.buildbucket.schedule_request(
        builder=compilator_name,
        swarming_parent_run_id=self.api.swarming.task_id,
        tags=self.api.buildbucket.tags(**{'hide-in-gerrit': 'pointless'}),
        properties=dict(revision=revision) if revision else {},
        gerrit_changes=gerrit_changes,
        bucket=bucket)
    return self.api.buildbucket.schedule(
        [request], step_name=self._add_suffix('trigger compilator'))[0]

  def launch_compilator_watcher(self, build_handle):
    """Follow the ongoing compilator build and stream the steps into this
    build.
    """
    cipd_pkg = 'infra/chromium/compilator_watcher/${platform}'
    compilator_watcher = self.api.cipd.ensure_tool(cipd_pkg, 'latest')
    sub_build = build_pb2.Build()
    sub_build.CopyFrom(build_handle)
    cmd = [
        compilator_watcher,
        '--',
        '-compilator-id',
        build_handle.id,
        '-get-swarming-trigger-props'
    ]
    build_url = self.api.buildbucket.build_url(build_id=build_handle.id)
    build_link = f'compilator build: {build_handle.id}'
    try:
      ret = self.api.step.sub_build(
          self._add_suffix('compilator steps'), cmd, sub_build)
      ret.presentation.links[build_link] = build_url
      return ret.step.sub_build
    except self.api.step.StepFailure as e:
      ret = self.api.step.active_result
      ret.presentation.links[build_link] = build_url
      sub_build = ret.step.sub_build
      if not sub_build:
        raise self.api.step.InfraFailure('sub_build missing from step') from e
      return sub_build


class LedCompilatorHandler(CompilatorHandler):
  def trigger_compilator(
      self,
      compilator_name,
      revision=None,
      bucket=None,
      gerrit_changes=V8OrchestratorApi.INHERIT,
  ):
    """Trigger a compilator build via led."""
    project = self.api.buildbucket.build.builder.project
    bucket = bucket or self.api.buildbucket.build.builder.bucket
    led_builder_id = f'{project}/{bucket}/{compilator_name}'
    with self.api.step.nest(self._add_suffix('trigger compilator')):
      led_job = self.api.led('get-builder', led_builder_id)
      led_job = led_job.with_injected_input_recipes()
      if revision:
        led_job = led_job.then('edit', '-p', f'revision="{revision}"')

      if gerrit_changes == self.api.v8_orchestrator.INHERIT:
        active_cl = self.api.tryserver.gerrit_change
        if active_cl:
          gerrit_cl_url = (
              f'https://{active_cl.host}/c/{active_cl.project}/+/'
              f'{active_cl.change}/{active_cl.patchset}')
          led_job = led_job.then('edit-cr-cl', gerrit_cl_url)
      elif gerrit_changes:  # pragma: nocover
        raise NotImplementedError()

      return led_job.then('launch').launch_result

  def launch_compilator_watcher(self, build_handle):
    """Collect the compilator led build from swarming. Streaming steps as in
    production is not available.
    """
    output_dir = self.api.path.mkdtemp()
    self.api.swarming.collect(
        self._add_suffix('collect led compilator build'),
        [build_handle.task_id],
        output_dir=output_dir)
    build_json = self.api.file.read_json(
        'read build.proto.json',
        output_dir.join(build_handle.task_id, 'build.proto.json'),
    )
    return json_format.ParseDict(
        build_json, build_pb2.Build(), ignore_unknown_fields=True)
