# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe for launching polymorphic builders.

The launcher enables creating a single polymorphic builder that can be
launched to perform operations on behalf of 1 or more target builders.
This prevents needing to create and maintain the configuration for
builders to mimic the standard builders when enabling alternate modes of
operation.
"""

from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto \
    import builder_common as builder_common_pb
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_modules.build.chromium_polymorphic.properties \
    import BuilderGroupAndName, TesterFilter
from PB.recipes.build.chromium_polymorphic.launcher import InputProperties


PROPERTIES = InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_polymorphic
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    futures,
    json,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium_polymorphic: chromium_polymorphic.API
  futures: futures.API
  json: json.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium_polymorphic: chromium_polymorphic.TEST_API
  json: json.TEST_API
  properties: properties.TEST_API
  step: step.TEST_API


def RunSteps(api: DEPS, properties):
  futures = [
      api.futures.spawn(_trigger_runner, api, target_builder,
                        properties.runner_builder)
      for target_builder in properties.target_builders
  ]
  # Get the result of the futures so that if one of them failed, an exception
  # will be raised
  for f in api.futures.wait(futures):
    f.result()


def _trigger_runner(api, target_builder, runner_builder_id):
  b = target_builder.builder_id
  with api.step.nest(f'{b.project}/{b.bucket}/{b.builder}'):
    tester_filter = (
        target_builder.tester_filter
        if target_builder.HasField('tester_filter') else None)
    properties = api.chromium_polymorphic.get_target_properties(
        b, tester_filter=tester_filter)
    dimensions = [
        common_pb.RequestedDimension(key=k, value=v)
        for k, v in target_builder.dimensions.items()
    ]
    api.buildbucket.schedule(
        [
            api.buildbucket.schedule_request(
                project=runner_builder_id.project,
                bucket=runner_builder_id.bucket,
                builder=runner_builder_id.builder,
                properties=properties,
                dimensions=dimensions,
                can_outlive_parent=True,
            ),
        ],
        # Save each sub build as a separate invocation. This is needed because
        # ResultDB only exports test suites for build whose "IsExportRoot" is
        # true, which is set when a build has not parent or explicitly requested
        # on the schedule request.
        include_sub_invs=False,
    )


def GenTests(api: TEST_DEPS):

  def properties_on_target_build(*, project, bucket, builder, properties):
    return api.chromium_polymorphic.properties_on_target_build(
        properties,
        step_name=f'{project}/{bucket}/{builder}.buildbucket.search')

  def check_scheduled(check, steps, step_name, properties):
    if not check(step_name in steps, f'step {step_name} was run'):
      return  # pragma: no cover

    req = api.json.loads(steps[step_name].logs['request'])
    schedule_props = req["requests"][0]["scheduleBuild"]["properties"]
    check(schedule_props == properties)

  yield api.test(
      'basic',
      api.properties(
          InputProperties(
              runner_builder=builder_common_pb.BuilderID(
                  project='fake-project',
                  bucket='fake-bucket',
                  builder='fake-runner-builder',
              ),
              target_builders=[
                  InputProperties.TargetBuilder(
                      builder_id=builder_common_pb.BuilderID(
                          project='fake-project',
                          bucket='fake-target-bucket',
                          builder='fake-target-builder-foo',
                      ),
                      dimensions={
                          'foo-dim-1': 'foo-dim-value-1',
                          'foo-dim-2': 'foo-dim-value-2',
                      },
                  ),
                  InputProperties.TargetBuilder(
                      builder_id=builder_common_pb.BuilderID(
                          project='fake-project',
                          bucket='fake-target-bucket',
                          builder='fake-target-builder-bar',
                      ),
                      dimensions={
                          'bar-dim-1': 'bar-dim-value-1',
                          'bar-dim-2': 'bar-dim-value-2',
                      },
                  ),
              ],
          )),
      properties_on_target_build(
          project='fake-project',
          bucket='fake-target-bucket',
          builder='fake-target-builder-foo',
          properties={
              'builder_group': 'fake-foo-group',
              'foo-prop-1': 'foo-prop-value-1',
              'foo-prop-2': 'foo-prop-value-2',
          },
      ),
      properties_on_target_build(
          project='fake-project',
          bucket='fake-target-bucket',
          builder='fake-target-builder-bar',
          properties={
              'builder_group': 'fake-bar-group',
              'bar-prop-1': 'bar-prop-value-1',
              'bar-prop-2': 'bar-prop-value-2',
          },
      ),
      api.post_check(
          check_scheduled,
          ('fake-project/fake-target-bucket/fake-target-builder-foo'
           '.buildbucket.schedule'),
          {
              '$build/chromium_polymorphic': {
                  'target_builder_group': 'fake-foo-group',
                  'target_builder_id': {
                      'bucket': 'fake-target-bucket',
                      'builder': 'fake-target-builder-foo',
                      'project': 'fake-project',
                  },
              },
          },
      ),
      api.post_check(
          check_scheduled,
          ('fake-project/fake-target-bucket/fake-target-builder-bar'
           '.buildbucket.schedule'),
          {
              '$build/chromium_polymorphic': {
                  'target_builder_group': 'fake-bar-group',
                  'target_builder_id': {
                      'bucket': 'fake-target-bucket',
                      'builder': 'fake-target-builder-bar',
                      'project': 'fake-project',
                  },
              },
          },
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'tester-filter',
      api.properties(
          InputProperties(
              runner_builder=builder_common_pb.BuilderID(
                  project='fake-project',
                  bucket='fake-bucket',
                  builder='fake-runner-builder',
              ),
              target_builders=[
                  InputProperties.TargetBuilder(
                      builder_id=builder_common_pb.BuilderID(
                          project='fake-project',
                          bucket='fake-target-bucket',
                          builder='fake-target-builder',
                      ),
                      tester_filter=TesterFilter(testers=[
                          BuilderGroupAndName(
                              group='fake-group',
                              builder='fake-target-tester',
                          ),
                      ]),
                  ),
              ],
          )),
      properties_on_target_build(
          project='fake-project',
          bucket='fake-target-bucket',
          builder='fake-target-builder',
          properties={
              'builder_group': 'fake-group',
          },
      ),
      api.post_check(
          check_scheduled,
          ('fake-project/fake-target-bucket/fake-target-builder'
           '.buildbucket.schedule'),
          {
              '$build/chromium_polymorphic': {
                  'target_builder_group': 'fake-group',
                  'target_builder_id': {
                      'bucket': 'fake-target-bucket',
                      'builder': 'fake-target-builder',
                      'project': 'fake-project',
                  },
                  'tester_filter': {
                      'testers': [{
                          'group': 'fake-group',
                          'builder': 'fake-target-tester',
                      }],
                  }
              },
          },
      ),
      api.post_process(post_process.DropExpectation),
  )
