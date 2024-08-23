# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Module for providing reproduction instructions on Milo"""

from collections.abc import Iterable
from typing import Any

from recipe_engine import recipe_api
from recipe_engine import step_data
from recipe_engine.config_types import Path
from recipe_engine.util import Placeholder

from RECIPE_MODULES.build.chromium_tests import steps

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import (instruction as
                                                       instruction_pb)
from PB.go.chromium.org.luci.resultdb.proto.v1 import resultdb


class ReproInstructionsApi(recipe_api.RecipeApi):

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._instructions = {}

  def update_invocation_instructions(
      self, *, step_name: str = 'update invocation instructions') -> None:
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
            instructions=self._instructions.values()))

  def create_step_instruction(
      self,
      tag: str,
      description: str,
      local_content: str,
      remote_content: str,
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
    Returns:
      A instruction_pb.Instruction that can be used in the invocation
    """
    local_instruction = instruction_pb.TargetedInstruction(
        content=local_content,
        targets=[
            instruction_pb.InstructionTarget.LOCAL,
        ])

    instruction = instruction_pb.Instruction(
        id=tag,
        descriptive_name=description[:100],
        type=instruction_pb.InstructionType.STEP_INSTRUCTION,
        targeted_instructions=[
            instruction_pb.TargetedInstruction(
                content=remote_content,
                targets=[
                    instruction_pb.InstructionTarget.REMOTE,
                ],
            ),
            local_instruction,
        ],
    )
    self._instructions[tag] = instruction

  def create_test_result_instruction(
      self,
      tag: str,
      description: str,
      local_content: str,
      remote_content: str,
      test_invocations: Iterable[str],
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
      local_content: The instruction itself to display to the user for the
        "local" tab
      remote_content: The instruction itself to display to the user for the
        "remote" tab
      test_invocations: If set this will also be used to identify the
        instruction as a TEST_INSTRUCTION. Filters the test instruction to only
        be seen when opening a test with the provided invocations
    Returns:
      A instruction_pb.Instruction that can be used in the invocation
    """
    local_instruction = instruction_pb.TargetedInstruction(
        content=local_content,
        targets=[
            instruction_pb.InstructionTarget.LOCAL,
        ])

    instruction = instruction_pb.Instruction(
        id=tag,
        descriptive_name=description[:100],
        type=instruction_pb.InstructionType.TEST_RESULT_INSTRUCTION,
        targeted_instructions=[
            instruction_pb.TargetedInstruction(
                content=remote_content,
                targets=[
                    instruction_pb.InstructionTarget.REMOTE,
                ],
            ),
            local_instruction,
        ],
        instruction_filter=instruction_pb.InstructionFilter(
            invocation_ids=instruction_pb.InstructionFilterByInvocationID(
                invocation_ids=test_invocations)),
    )
    self._instructions[tag] = instruction

  def get_utr_instruction(self,
                          command: str,
                          test_names: Iterable[str],
                          extra_args: Iterable[str] | None = None) -> str:
    """ Provides the Universal Test Runner (UTR) steps to reproduce a step

    Adds any provided local and remote instructions for to the provided by
    attaching the tag and instruction id. This must be called before the step
    has been finalized.

    Args:
      command: The UTR command to run (eg 'compile', 'compile-and-run')
      test_names: Test names to invoke UTR with or none to compile all
      extra_args: Any extra args to append to the command (eg a test filter)
    Returns:
      The UTR command that can be run from chromium/src checkout
    """

    def quote_as_needed(s):
      if len(s.split()) > 1:
        return '"' + s + '"'
      return s

    utr_cmd = [
        'vpython3',
        'tools/utr',
        '-p',
        quote_as_needed(self.m.buildbucket.build.builder.project),
        '-B',
        quote_as_needed(self.m.led.shadowed_bucket or
                        self.m.buildbucket.build.builder.bucket),
        '-b',
        quote_as_needed(
            self.m.buildbucket.build.builder.builder.replace('-compilator',
                                                             '')),
    ]
    if not test_names:
      test_names = []
    for test in test_names:
      utr_cmd.extend(['-t', test])
    utr_cmd.append(command)

    if extra_args:
      utr_cmd.extend(extra_args)

    utr_cmd = ' '.join(utr_cmd)
    utr_readme_url = 'https://chromium.googlesource.com/chromium/src/+/main/tools/utr/README.md'
    lines = []
    lines.append(
        f'[UTR]({utr_readme_url}) command to reproduce from your Chromium '
        'checkout:')
    lines.append('```' + utr_cmd + '```')
    lines.append('')
    return '<br/>'.join(lines)
