# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Module for providing reproduction instructions on Milo"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
import re

from recipe_engine import recipe_api
from recipe_engine import step_data
from recipe_engine.config_types import Path
from recipe_engine.util import Placeholder

from RECIPE_MODULES.build.chromium_tests import steps

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  instruction as instruction_pb,
)
from PB.go.chromium.org.luci.resultdb.proto.v1 import resultdb

# Instruction content is limited to 10KB
# https://chromium.googlesource.com/infra/luci/recipes-py/+/b44da3c0/recipe_proto/go.chromium.org/luci/resultdb/proto/v1/instruction.proto#109
_TARGET_INSTRUCTIONS_LIMIT = 10240


class ReproInstructionsApi(recipe_api.RecipeApi):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._instructions = {}

  @property
  def step_ids(self) -> Iterable[str]:
    """Get the ids for instructions of the added steps."""
    return [
      key
      for key, value in self._instructions.items()
      if value.type == instruction_pb.InstructionType.STEP_INSTRUCTION
    ]

  def _limit_content(self, instructions: str) -> str:
    if not instructions or len(instructions) <= _TARGET_INSTRUCTIONS_LIMIT:
      return instructions
    return f'{instructions[: _TARGET_INSTRUCTIONS_LIMIT - 3]}...'

  def update_invocation_instructions(
    self, *, step_name: str = 'update invocation instructions'
  ) -> None:
    """Update the rdb invocation to include reproduction instructions.

    Attaches all collected steps to the invocation. Each call will overwrite
    any existing instruction so all instructions must be written to add any.

    Args:
      step_name: name to display on the step
    """
    if not self.m.resultdb.enabled:
      return

    self.m.resultdb.update_invocation(
      step_name=step_name,
      instructions=instruction_pb.Instructions(
        instructions=self._instructions.values()
      ),
      raise_on_failure=False,
    )

  def create_step_instruction(
    self,
    tag: str,
    description: str,
    *,
    local_content: str = None,
    remote_content: str = None,
    prebuilt_content: str = None,
    local_dependency: instruction_pb.InstructionDependency | None = None,
    remote_dependency: instruction_pb.InstructionDependency | None = None,
  ) -> None:
    """Create a reproduction instruction

    Creates a instruction_pb.Instruction instruction that can be added to the
    invocation. These are displayed on the milo page but not the test result

    Args:
      tag: What to tag the instruction with. Steps that want to display this
        instruction in milo will need to have this same value attached to their
        step result
      description: Descriptive name of the instruction
      local_content: The instruction itself to display to the user for the
        "local" tab
      remote_content: The instruction itself to display to the user for the
        "remote" tab
      prebuilt_content: The instruction itself to display to the user for the
        "prebuilt" tab
      local_dependency: The InstructionDependency the local instructions will
        require be run before they themselves are invoked
      remote_dependency: The InstructionDependency the remote instructions will
        require be run before they themselves are invoked
    Returns:
      A instruction_pb.Instruction that can be used in the invocation
    """
    local_content = self._limit_content(local_content)
    remote_content = self._limit_content(remote_content)
    prebuilt_content = self._limit_content(prebuilt_content)

    local_instruction = None
    if local_content:
      local_instruction = instruction_pb.TargetedInstruction(
        content=local_content,
        targets=[
          instruction_pb.InstructionTarget.LOCAL,
        ],
      )
    remote_instruction = None
    if remote_content:
      remote_instruction = instruction_pb.TargetedInstruction(
        content=remote_content,
        targets=[
          instruction_pb.InstructionTarget.REMOTE,
        ],
      )
    prebuilt_instruction = None
    if prebuilt_content:
      prebuilt_instruction = instruction_pb.TargetedInstruction(
        content=prebuilt_content,
        targets=[
          instruction_pb.InstructionTarget.PREBUILT,
        ],
      )
    if local_dependency:
      local_instruction.dependencies.append(local_dependency)
    if remote_dependency:
      remote_instruction.dependencies.append(remote_dependency)

    instruction = instruction_pb.Instruction(
      id=tag,
      descriptive_name=description[:100],
      type=instruction_pb.InstructionType.STEP_INSTRUCTION,
    )
    if local_content:
      instruction.targeted_instructions.append(local_instruction)
    if remote_content:
      instruction.targeted_instructions.append(remote_instruction)
    if prebuilt_content:
      instruction.targeted_instructions.append(prebuilt_instruction)
    self._instructions[tag] = instruction

  def create_test_result_instruction(
    self,
    tag: str,
    description: str,
    test_invocations: Iterable[str],
    *,
    local_content: str = None,
    remote_content: str = None,
    prebuilt_content: str = None,
    local_dependency: instruction_pb.InstructionDependency | None = None,
    remote_dependency: instruction_pb.InstructionDependency | None = None,
    recursive: bool = False,
  ) -> instruction_pb.Instruction:
    """Create a reproduction instruction

    Creates a instruction_pb.Instruction instruction that can be  added to the
    invocation for test results. These are displayed on the test results and
    not directly on the build's milo page

    Args:
      tag: What to tag the instruction with. Steps that want to display this
        instruction in milo will need to have this same value attached to their
        step result
      description: Descriptive name of the instruction
      test_invocations: Filters the test instruction to only be seen when
        opening a test with one of the provided invocations.
      local_content: The instruction itself to display to the user for the
        "local" tab
      remote_content: The instruction itself to display to the user for the
        "remote" tab
      prebuilt_content: The instruction itself to display to the user for the
        "prebuilt" tab
      local_dependency: The InstructionDependency the local instructions will
        require be run before they themselves are invoked
      remote_dependency: The InstructionDependency the remote instructions will
        require be run before they themselves are invoked
      recursive: Whether or not to set the instruction filtering to recursive.
        This is needed if the test results belong to a child builder's
        invocation.
    Returns:
      A instruction_pb.Instruction that can be used in the invocation
    """
    local_content = self._limit_content(local_content)
    remote_content = self._limit_content(remote_content)
    prebuilt_content = self._limit_content(prebuilt_content)

    local_instruction = instruction_pb.TargetedInstruction(
      content=local_content,
      targets=[
        instruction_pb.InstructionTarget.LOCAL,
      ],
    )
    if local_dependency:
      local_instruction.dependencies.append(local_dependency)

    remote_instruction = instruction_pb.TargetedInstruction(
      content=remote_content,
      targets=[
        instruction_pb.InstructionTarget.REMOTE,
      ],
    )
    if remote_dependency:
      remote_instruction.dependencies.append(remote_dependency)

    prebuilt_instruction = instruction_pb.TargetedInstruction(
      content=prebuilt_content,
      targets=[
        instruction_pb.InstructionTarget.PREBUILT,
      ],
    )

    instruction = instruction_pb.Instruction(
      id=tag,
      descriptive_name=description[:100],
      type=instruction_pb.InstructionType.TEST_RESULT_INSTRUCTION,
      instruction_filter=instruction_pb.InstructionFilter(
        invocation_ids=instruction_pb.InstructionFilterByInvocationID(
          invocation_ids=test_invocations, recursive=recursive
        )
      ),
    )

    if remote_content:
      instruction.targeted_instructions.append(remote_instruction)
    if local_content:
      instruction.targeted_instructions.append(local_instruction)
    if prebuilt_content:
      instruction.targeted_instructions.append(prebuilt_instruction)

    self._instructions[tag] = instruction

  def tag_for_step(self, step_name: str):
    return (
      f'{step_name.lower()}_repro_instructions'.replace(' ', '_')
      .replace('(', '')
      .replace(')', '')
      .replace('|', '')[:100]
    )

  def add_step_instruction(
    self,
    step_result: step_data.StepData,
    *,
    local_content: str | None = None,
    remote_content: str | None = None,
    local_dependency: instruction_pb.InstructionDependency | None = None,
    remote_dependency: instruction_pb.InstructionDependency | None = None,
  ) -> None:
    """Adds step instructions for a step using its step result

    Adds any provided local and remote instructions to the invoction. Handles
    attaching the tag and instruction id. This must be called before the step
    has been finalized.

    Args:
      step_result: StepData of the step to add instructions for. Must not be
        finalized
      local_content: A string explaining how to reproduce the step locally
      remote_instruction: A string explaining how to reproduce the step remotely
      local_dependency: The InstructionDependency the local instructions will
        require be run before they themselves are invoked
      remote_dependency: The InstructionDependency the remote instructions will
        require be run before they themselves are invoked
    """
    tag = self.tag_for_step(step_result.name)
    self.create_step_instruction(
      tag,
      f'{step_result.name} instructions',
      local_content=local_content,
      remote_content=remote_content,
      local_dependency=local_dependency,
      remote_dependency=remote_dependency,
    )
    step_result.presentation.tags['resultdb.instruction.id'] = tag

  def process_sub_build(self, sub_build: build_pb2.Build) -> None:
    """Adds instructions from a sub build

    Adds instructions from another build to the current invocation. This is
    necessary for instances like compilator where steps from another build are
    displayed in Milo. These steps are already tagged but the actual
    instructions are only on the sub build's invocation which need to be
    retreived to resolve the instruction tag.

    Args:
      sub_build: The build containing instructions that need to be added to the
        current build.
    """
    inv_name = sub_build.infra.resultdb.invocation

    instructions = self.m.resultdb.get_invocation_instructions(inv_name)
    for instruction in instructions.instructions:
      self._instructions[instruction.id] = instruction

  def trigger_properties(
    self,
  ) -> dict[str, Iterable[instruction_pb.InstructionDependency]]:
    """Provides the instructions on the current build as dependencies in a dict

    The dict provided by this will be digested in get_dependency when provided
    as an input. This allow instructions to depend on each other from parent
    and child builds
    """
    dependencies = []
    dep_invocation = 'build-' + str(self.m.buildbucket.build.id)
    for instruction in self._instructions.values():
      dependencies.append(
        {
          'invocation_id': dep_invocation,
          'instruction_id': instruction.id,
        }
      )
    return {'instruction_dependencies': dependencies}

  def get_step_instruction_tag(self, step_id_re: str) -> str:
    """Returns the last step id that matches the provided regex

    Args:
      step_id_re: Regex to run against step ids to validate the step
    """
    for step_id in reversed(self.step_ids):
      if re.match(step_id_re, step_id):
        return step_id
    return None

  def get_dependency(
    self,
    step_id_re: str,
  ) -> instruction_pb.InstructionDependency:
    """Get a dependency for a targeted step potentially from a parent build

    Some instructions depend on an instruction belonging to a parent build
    such as Tester builders. If the build has a parent id (eg a tester that was
    triggered by a builder) the parent invocation is checked first. Traverses
    the id's in reverse order to get the latest step that matches.

    Args:
      step_id_re: Regex to run against step ids to validate the step
    """
    parent_dependencies = self.m.properties.get('instruction_dependencies', [])

    # If we have a parent build try for a step on that invocation
    for dependency in reversed(parent_dependencies):
      if (
        'invocation_id' in dependency
        and 'instruction_id' in dependency
        and re.match(step_id_re, dependency['instruction_id'])
      ):
        return instruction_pb.InstructionDependency(
          invocation_id=dependency['invocation_id'],
          instruction_id=dependency['instruction_id'],
        )

    dep_invocation = 'build-' + str(self.m.buildbucket.build.id)
    for step_id in reversed(self.step_ids):
      if re.match(step_id_re, step_id):
        return instruction_pb.InstructionDependency(
          invocation_id=dep_invocation,
          instruction_id=step_id,
        )
    return None
