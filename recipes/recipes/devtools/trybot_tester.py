# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Recipe for DevTools trybot tester."""

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from recipe_engine.post_process import DoesNotRunRE, DropExpectation

from google.protobuf import json_format
from google.protobuf import struct_pb2

from PB.recipes.build.devtools.trybot_tester import InputProperties

from RECIPE_MODULES.build.devtools.api_tests_runner import ApiTests
from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.e2e_tests_runner import E2ETests
from RECIPE_MODULES.build.devtools.test_phases import run_test_pipelines
from RECIPE_MODULES.build.devtools.unit_tests_runner import UnitTests
from RECIPE_MODULES.build.devtools.scripts_tests_runner import ScriptsTests


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  builder_group,
  chromium_swarming,
  devtools,
  v8,
  v8_orchestrator,
)
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  file,
  futures,
  json,
  path,
  platform,
  properties,
  raw_io,
  resultdb,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  chromium_swarming: chromium_swarming.API
  context: context.API
  devtools: devtools.API
  file: file.API
  futures: futures.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  tryserver: tryserver.API
  v8: v8.API
  v8_orchestrator: v8_orchestrator.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  json: json.TEST_API
  path: path.TEST_API
  properties: properties.TEST_API
  step: step.TEST_API


PROPERTIES = InputProperties


def RunSteps(api: DEPS, properties):
  builder_config = properties.builder_config or 'Release'
  target_os = properties.target_os
  target_cpu = properties.target_cpu

  comp_props, maybe_raw_result = api.v8_orchestrator.orchestrated_compilation(
    properties.compilator_name
  )
  if maybe_raw_result:
    return maybe_raw_result

  cas_digest = comp_props['cas_digest']
  affected_files = (
    list(comp_props['affected_files']) if 'affected_files' in comp_props else []
  )

  trigger = SwarmingTrigger(
    api,
    cas_digest,
    target_dimensions={
      'cpu': target_cpu,
      'os': target_os,
      'pool': 'chromium.tests',
    },
  )
  tests = [
    UnitTests(api, trigger, builder_config, False, 'Unit Tests'),
    UnitTests(
      api,
      trigger,
      builder_config,
      False,
      'Unit Tests (node)',
      node_unit_tests=True,
    ),
    ApiTests(api, trigger, builder_config, 'API Tests'),
    E2ETests(api, trigger, builder_config, 'E2E Tests'),
    ScriptsTests(api, trigger, builder_config, 'Scripts Tests', target_os),
  ]
  tests = [t for t in tests if not t.skip()]

  results = run_test_pipelines(api, tests, affected_files=affected_files)
  return results.raw_result()


def GenTests(api: TEST_DEPS):
  test_files = [
    'front_end/panels/timeline/timeline_test.ts',
    'test/e2e/helpers/datagrid-helpers.ts',
    'test/e2e/helpers/performance-helpers.ts',
    'test/e2e/helpers/sources-helpers.ts',
    'test/e2e/helpers/visual-logging-helpers.ts',
    'test/e2e/performance/selector-stats-tracing_test.ts',
    'test/e2e/BUILD.gn',
    'test/e2e/performance/BUILD.gn',
    'test/e2e/performance/selector-stats-tracing_test.ts',
    'test/e2e/performance/skip_test.ts',
    'test/e2e/shared/frontend-helper.ts',
    'test/e2e/shared/page-wrapper.ts',
    'test/shared/helper.ts',
  ]
  default_output_properties = {
    "compilator_properties": {
      "cas_digest": '1234567/890',
      "affected_files": test_files,
    },
  }

  def subbuild_data(
    output_properties=None, summary='pass', status=common_pb.SUCCESS
  ):
    output_properties = output_properties or {}
    sub_build = build_pb2.Build(
      id=54321,
      status=status,
      summary_markdown=summary,
      output={
        'properties': json_format.Parse(
          api.json.dumps(output_properties), struct_pb2.Struct()
        )
      },
    )
    return api.step_data('compilator steps', api.step.sub_build(sub_build))

  def test(name, *args, **kwargs):
    return api.test(
      name,
      api.buildbucket.try_build(builder='dtf_linux_rel'),
      api.properties(
        compilator_name='dtf_linux_compiler',
        target_os='ubuntu',
        target_cpu='x64',
      ),
      *args,
      **kwargs,
    )

  yield test(
    'basic',
    subbuild_data(default_output_properties),
    api.step_data(
      'find new tests.parse description',
      api.json.output(
        {'Skip-Flake-Detection': ['test/e2e/performance/skip_test.ts']}
      ),
    ),
    # Update this path when the order of swarming tasks changes.
    api.path.exists(api.path.cleanup_dir.joinpath('tmp_tmp_4/1/goldens')),
  )

  yield test(
    'compilator failed',
    subbuild_data({}, 'fail', common_pb.FAILURE),
    api.post_process(DoesNotRunRE, 'Pipeline .*'),
    api.post_process(DoesNotRunRE, 'find new tests.*'),
    api.post_process(DropExpectation),
    status='FAILURE',
  )
