# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  instruction as instruction_pb,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_utr, repro_instructions
from RECIPE_MODULES.recipe_engine import (
  assertions,
  buildbucket,
  path,
  properties,
  resultdb,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  buildbucket: buildbucket.API
  chromium_utr: chromium_utr.API
  path: path.API
  properties: properties.API
  repro_instructions: repro_instructions.API
  resultdb: resultdb.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  properties: properties.TEST_API
  resultdb: resultdb.TEST_API


def RunSteps(api: DEPS):
  api.repro_instructions.source_dir = api.path.cache_dir / 'builder' / 'src'
  _ = api.repro_instructions.source_dir
  api.repro_instructions.build_dir = (
    api.path.cache_dir / 'builder' / 'src' / 'out' / 'Release'
  )
  _ = api.repro_instructions.build_dir

  # Create a step with instructions
  _ = steps.MockTestSpec.create('mock test').get_test(api)

  step_result = api.step.empty('test')
  api.repro_instructions.add_step_instruction(
    step_result, local_content='root/run', remote_content='tools/utr'
  )

  # Create a step that depends on the last one
  step_result = api.step.empty('test2')
  dependency_step = api.repro_instructions.get_dependency(r'.*')
  # Parent invocations are cached, calling again won't create another rpc
  dependency_step = api.repro_instructions.get_dependency(r'.*')
  api.repro_instructions.add_step_instruction(
    step_result,
    local_content='foo',
    remote_content='bar',
    local_dependency=dependency_step,
    remote_dependency=dependency_step,
  )

  # create_step_instruction doesn't tag the step or use the step_result
  api.repro_instructions.create_step_instruction(
    'custom_tag',
    'custom description',
    local_content='foo',
    remote_content='bar',
    prebuilt_content='baz' * 10240,
    local_dependency=dependency_step,
    remote_dependency=dependency_step,
  )

  # Finding a non-existent test doesn't raise an exception
  api.repro_instructions.get_dependency(r'asdfasdf')

  # Add steps from another build
  api.repro_instructions.process_sub_build(
    build_pb2.Build(
      infra=build_pb2.BuildInfra(
        resultdb=build_pb2.BuildInfra.ResultDB(
          invocation='invocations/build:1234'
        )
      )
    )
  )

  api.repro_instructions.create_test_result_instruction(
    'tag',
    'description',
    ['invocation-id'],
    local_content='run foo.bar locally',
    remote_content='run foo.bar remotely',
    prebuilt_content='run foo.bar with cas',
    local_dependency=dependency_step,
    remote_dependency=dependency_step,
  )
  api.repro_instructions.update_invocation_instructions()

  api.repro_instructions.trigger_properties()

  api.repro_instructions.get_step_instruction_tag(r'.*')
  api.repro_instructions.get_step_instruction_tag(r'asdfasdf')


def GenTests(api: TEST_DEPS):
  instructions = instruction_pb.Instructions(
    instructions=[instruction_pb.Instruction(id='instruction')]
  )
  yield api.test(
    'full',
    api.buildbucket.generic_build(builder='fake builder'),
    api.resultdb.get_invocation_instructions(instructions=instructions),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'tester_builder',
    api.buildbucket.generic_build(builder='fake builder'),
    api.properties(
      instruction_dependencies=[
        {'invocation_id': 'build-12341234', 'instruction_id': 'foo_step_id'}
      ]
    ),
    api.resultdb.get_invocation_instructions(instructions=instructions),
    api.resultdb.get_invocation_instructions(instructions=instructions),
    api.post_process(
      post_process.DoesNotRun, "get_invocation_instructions (3)"
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'rdb_disabled',
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'try_builder_rejects_untrusted_dependency',
    api.buildbucket.try_build(builder='linux-rel'),
    api.properties(
      instruction_dependencies=[
        {'invocation_id': 'build-999999', 'instruction_id': 'foo_step_id'}
      ]
    ),
    api.resultdb.get_invocation_instructions(instructions=instructions),
    api.post_process(
      post_process.LogDoesNotContain,
      'update invocation instructions',
      'json.input',
      ['build-999999'],
    ),
    api.post_process(
      post_process.LogContains,
      'update invocation instructions',
      'json.input',
      ['build-8945511751514863184'],
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'ancestor_ids_validates_parent',
    api.buildbucket.try_build(builder='linux-rel', ancestor_ids=[111111]),
    api.properties(
      instruction_dependencies=[
        {'invocation_id': 'build-111111', 'instruction_id': 'foo_step_id'},
        {'invocation_id': 'build-999999', 'instruction_id': 'foo_step_id'},
      ]
    ),
    api.resultdb.get_invocation_instructions(instructions=instructions),
    api.post_process(
      post_process.LogContains,
      'update invocation instructions',
      'json.input',
      ['build-111111'],
    ),
    api.post_process(
      post_process.LogDoesNotContain,
      'update invocation instructions',
      'json.input',
      ['build-999999'],
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'ci_scheduler_tester_allows_valid_dependency',
    api.buildbucket.ci_build(builder='linux-tester'),
    api.properties(
      instruction_dependencies=[
        {'invocation_id': 'build-12341234', 'instruction_id': 'foo_step_id'}
      ]
    ),
    api.resultdb.get_invocation_instructions(instructions=instructions),
    api.post_process(
      post_process.LogContains,
      'update invocation instructions',
      'json.input',
      ['build-12341234'],
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'try_beta_rejects_untrusted_dependency',
    api.buildbucket.try_build(bucket='try-beta', builder='linux-rel'),
    api.properties(
      instruction_dependencies=[
        {'invocation_id': 'build-999999', 'instruction_id': 'foo_step_id'}
      ]
    ),
    api.resultdb.get_invocation_instructions(instructions=instructions),
    api.post_process(
      post_process.LogDoesNotContain,
      'update invocation instructions',
      'json.input',
      ['build-999999'],
    ),
    api.post_process(
      post_process.LogContains,
      'update invocation instructions',
      'json.input',
      ['build-8945511751514863184'],
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'non_sequence_instruction_dependencies',
    api.buildbucket.ci_build(builder='linux-tester'),
    api.properties(instruction_dependencies='build-999999'),
    api.resultdb.get_invocation_instructions(instructions=instructions),
    api.post_process(
      post_process.LogDoesNotContain,
      'update invocation instructions',
      'json.input',
      ['build-999999'],
    ),
    api.post_process(
      post_process.LogContains,
      'update invocation instructions',
      'json.input',
      ['build-8945511751514863184'],
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'malformed_instruction_dependencies',
    api.buildbucket.ci_build(builder='linux-tester'),
    api.properties(
      instruction_dependencies=[
        {
          'invocation_id': 'invocations/build:999',
          'instruction_id': 'foo_step_id',
        },
        {'invocation_id': 'build-123\n', 'instruction_id': 'foo_step_id'},
        {'invocation_id': 'build-१२३', 'instruction_id': 'foo_step_id'},
        {'invocation_id': 123, 'instruction_id': 'foo_step_id'},
        'bad',
      ]
    ),
    api.resultdb.get_invocation_instructions(instructions=instructions),
    api.post_process(
      post_process.LogDoesNotContain,
      'update invocation instructions',
      'json.input',
      ['invocations/build:999'],
    ),
    api.post_process(
      post_process.LogDoesNotContain,
      'update invocation instructions',
      'json.input',
      ['build-123\\n'],
    ),
    api.post_process(
      post_process.LogContains,
      'update invocation instructions',
      'json.input',
      ['build-8945511751514863184'],
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'gerrit_changes_rejects_untrusted_dependency',
    api.buildbucket.try_build(bucket='cq', builder='linux-rel'),
    api.properties(
      instruction_dependencies=[
        {'invocation_id': 'build-999999', 'instruction_id': 'foo_step_id'}
      ]
    ),
    api.resultdb.get_invocation_instructions(instructions=instructions),
    api.post_process(
      post_process.LogDoesNotContain,
      'update invocation instructions',
      'json.input',
      ['build-999999'],
    ),
    api.post_process(
      post_process.LogContains,
      'update invocation instructions',
      'json.input',
      ['build-8945511751514863184'],
    ),
    api.post_process(post_process.DropExpectation),
  )
