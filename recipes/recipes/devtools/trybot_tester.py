# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from recipe_engine.post_process import (DoesNotRunRE, DropExpectation, MustRun)

from google.protobuf import json_format
from google.protobuf import struct_pb2

from PB.recipes.build.devtools.trybot_tester import InputProperties

from RECIPE_MODULES.build.devtools.api_tests_runner import ApiTests
from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.e2e_tests_runner import E2ENonHostedTests
from RECIPE_MODULES.build.devtools.test_phases import run_test_pipelines
from RECIPE_MODULES.build.devtools.unit_tests_runner import UnitTests
from RECIPE_MODULES.build.devtools.lint_check import LintCheck
from RECIPE_MODULES.build.devtools.performance_tests_runner import PerformanceTests
from RECIPE_MODULES.build.devtools.scripts_tests_runner import ScriptsTests


DEPS = [
    'builder_group',
    'chromium_swarming',
    'devtools',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/platform',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'recipe_engine/futures',
    'v8_orchestrator',
    'v8',
]

PROPERTIES = InputProperties


def RunSteps(api, properties):
  builder_config = properties.builder_config or 'Release'
  target_os = properties.target_os
  target_cpu = properties.target_cpu

  api.devtools.configure(builder_config, properties.is_official_build,
                         properties.devtools_skip_typecheck)
  api.devtools.update()

  comp_props, maybe_raw_result = api.v8_orchestrator.orchestrated_compilation(
      properties.compilator_name)
  if maybe_raw_result:
    return maybe_raw_result

  cas_digest = comp_props['cas_digest']

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
          node_unit_tests=True),
      ApiTests(api, trigger, builder_config, 'API Tests'),
      E2ENonHostedTests(api, trigger, builder_config, 'E2E Tests (non-hosted)'),
      LintCheck(api, trigger, builder_config, 'Lint Check', target_os),
      ScriptsTests(api, trigger, builder_config, 'Scripts Tests', target_os),
      PerformanceTests(api, trigger, builder_config, 'Performance Tests',
                       target_os)
  ]
  tests = [t for t in tests if not t.skip()]

  results = run_test_pipelines(api, tests)
  return results.raw_result()

def GenTests(api):
  default_output_properties = {
      "compilator_properties": {
          "cas_digest": '1234567/890',
          "e2e_test_list": 'test1.ts\ntest2.ts\n',
          "e2e_non_hosted_test_list": 'test11.ts\ntest22.ts\n',
      },
  }

  def subbuild_data(output_properties=None,
                    summary='pass',
                    status=common_pb.SUCCESS):
    output_properties = output_properties or {}
    sub_build = build_pb2.Build(
        id=54321,
        status=status,
        summary_markdown=summary,
        output=dict(
            properties=json_format.Parse(
                api.json.dumps(output_properties), struct_pb2.Struct())))
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
          api.json.output({
              'Skip-Flake-Detection': [
                  'test/e2e_non_hosted/performance/skip_test.ts'
              ]
          })),
      api.override_step_data(
          'find new tests.git diff',
          stdout=api.raw_io.output_text('\n'.join([
              'front_end/panels/timeline/timeline_test.ts',
              'test/e2e/helpers/datagrid-helpers.ts',
              'test/e2e/helpers/performance-helpers.ts',
              'test/e2e/helpers/sources-helpers.ts',
              'test/e2e/helpers/visual-logging-helpers.ts',
              'test/e2e/performance/selector-stats-tracing_test.ts',
              'test/e2e_non_hosted/BUILD.gn',
              'test/e2e_non_hosted/performance/BUILD.gn',
              'test/e2e_non_hosted/performance/selector-stats-tracing_test.ts',
              'test/e2e_non_hosted/performance/skip_test.ts',
              'test/e2e_non_hosted/shared/frontend-helper.ts',
              'test/e2e_non_hosted/shared/page-wrapper.ts',
              'test/shared/helper.ts',
          ]))),
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
