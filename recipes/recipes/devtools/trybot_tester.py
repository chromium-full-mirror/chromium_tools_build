# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from recipe_engine.post_process import (DoesNotRunRE, DropExpectation)

from google.protobuf import json_format
from google.protobuf import struct_pb2

from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.e2e_tests_runner import E2ETests, E2ETestDivider, write_test_list
from RECIPE_MODULES.build.devtools.interactions_tests_runner import InteractionsTests
from RECIPE_MODULES.build.devtools.test_phases import FirstRunPhase, ExonerationPhase
from RECIPE_MODULES.build.devtools.unit_tests_runner import UnitTests
from RECIPE_MODULES.build.devtools.lint_check import LintCheck

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
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'v8_orchestrator',
]

PROPERTIES = {
    'builder_config':
        Property(
            kind=str,
            help='Configuration name for the builder (Debug/Release)',
            default='Release'),
    'is_official_build':
        Property(
            kind=bool,
            help='Turn the is_official_build gn flag on (default off)',
            default=False),
    'devtools_skip_typecheck':
        Property(
            kind=bool,
            help='Turn the devtools_skip_typecheck gn flag on (default off)',
            default=False),
    'compilator_name':
        Property(kind=str, help='Compilator name'),
    'target_os':
        Property(kind=str, help='Target OS for swarming test tasks'),
    'target_cpu':
        Property(
            kind=str, help='Target cpu architecture for swarming test tasks'),
}


def RunSteps(api, builder_config, is_official_build, devtools_skip_typecheck,
             compilator_name, target_os, target_cpu):
  api.devtools.configure(builder_config, is_official_build,
                         devtools_skip_typecheck)
  update_result = api.devtools.update()

  source_dir = update_result.source_root.path
  comp_props, maybe_raw_result = api.v8_orchestrator.orchestrated_compilation(
      compilator_name)
  if maybe_raw_result:
    return maybe_raw_result

  # TODO(liviurau): Refactor this to make the divider take the file list a
  # direct argument. Eventually make it so we do not even need the list maybe
  # using a hash based stable sharding and ordering on the test runner side.
  write_test_list(api, source_dir, builder_config, comp_props['e2e_test_list'])

  cas_digest = comp_props['cas_digest']

  divider = E2ETestDivider(api, source_dir, builder_config)
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
      UnitTests(api, source_dir, trigger, builder_config, False, 'Unit Tests'),
      InteractionsTests(api, source_dir, trigger, builder_config,
                        'Interactions Tests'),
      E2ETests(api, source_dir, trigger, builder_config, 'E2E Tests', divider),
      LintCheck(api, source_dir, trigger, builder_config, 'Lint Check',
                api.devtools.lookup_command(source_dir, 'lint'), target_os),
  ]
  tests = [t for t in tests if not t.skip()]

  FirstRunPhase(api).run_all(tests)
  results = ExonerationPhase(api).run_all(tests)
  return results.raw_result()


def GenTests(api):
  default_output_properties = {
      "compilator_properties": {
          "cas_digest": '1234567/890',
          "e2e_test_list": 'test1.ts\ntest2.ts\n',
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
        api.buildbucket.try_build(builder='dtf_linux'),
        api.properties(
            compilator_name='dtf_linux_compiler',
            target_os='Linux',
            target_cpu='x64',
        ),
        *args,
        **kwargs,
    )

  yield test(
      'basic',
      subbuild_data(default_output_properties),
  )

  yield test(
      'compilator failed',
      subbuild_data({}, 'fail', common_pb.FAILURE),
      api.post_process(DoesNotRunRE, 'Run tests.*'),
      api.post_process(DoesNotRunRE, 'Flake exonaration attempt.*'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
