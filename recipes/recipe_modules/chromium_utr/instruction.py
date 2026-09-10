# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Creates the instruction to run UTR."""

from __future__ import annotations

from recipe_engine import recipe_api
from recipe_engine import step_data
from collections.abc import Iterable

from RECIPE_MODULES.build import chromium_types


def get_utr_instruction(
  command: str,
  project: str,
  bucket: str,
  builder: str,
  test_names: Iterable[str],
  utr_flags: Iterable[str] | None = None,
  extra_args: Iterable[str] | None = None,
  include_preface_text: bool = True,
) -> str:
  """Provides the Universal Test Runner (UTR) steps to reproduce a step

  Adds any provided local and remote instructions for to the provided by
  attaching the tag and instruction id. This must be called before the step
  has been finalized.

  Args:
    command: The UTR command to run (eg 'compile', 'compile-and-run')
    project: LUCI project of the builder to embed in the UTR cmd.
    bucket: LUCI bucket of the builder to embed in the UTR cmd.
    builder: LUCI builder of the builder to embed in the UTR cmd.
    test_names: Test names to invoke UTR with or none to compile all
    utr_flags: Any extra UTR flags to append to the command (eg --reuse-task)
    extra_args: Any extra args to append after the command (eg a test filter)
    include_preface_text: If True, prints a generic description before the
      UTR cmd.
  Returns:
    The UTR command that can be run from chromium/src checkout
  """

  def sanitize_arg(s):
    s = s.replace('"', '\\"').replace("'", "\\'")
    if len(s.split()) > 1:
      return '"' + s + '"'
    return s

  def gen_cmd(skip_siso=False):
    utr_cmd = [
      'vpython3',
      'tools/utr',
      '-p',
      project,
      '-B',
      bucket,
      '-b',
      builder,
    ]
    if skip_siso:
      utr_cmd += ['--no-siso']
    if utr_flags:
      utr_cmd.extend(utr_flags)
    for test in test_names or []:
      utr_cmd.extend(['-t', test])
    utr_cmd.append(command)
    if extra_args:
      utr_cmd.extend(extra_args)
    return ' '.join([sanitize_arg(arg) for arg in utr_cmd])

  lines = []
  if include_preface_text:
    utr_readme_url = 'https://chromium.googlesource.com/chromium/src/+/main/tools/utr/README.md'
    lines.append(
      f'[UTR]({utr_readme_url}) command to reproduce from your Chromium '
      'checkout:'
    )
  lines.append('```' + gen_cmd() + '```')
  lines.append('')

  if not project.startswith('chrome'):
    non_googler_cmd = gen_cmd(skip_siso=True)
    non_googler_line = (
      '<details><summary>For non-Googlers, use this command</summary>'
    )
    non_googler_line += f'```{non_googler_cmd}```</details>'
    lines.append(non_googler_line)

  return '<br/>'.join(lines)


def get_utr_compile_instruction(
  chromium_api: recipe_api.RecipeApi,
  step_result: step_data.StepData,
  builder_id: chromium_types.BuilderId,
) -> None:
  """Apply the compile instruction for the provided step_result

  Creates and adds the instruction for compiling using the UTR for the provided
  step. This does not update the invocation which needs to be called still to
  display the instruction.

  Args:
    chromium_api: Chromium api that has the required modules as dependencies
    step_result: The compile step's returned stepData
    builder_id: ID of the builder to use for the instruction
  """
  # UTR prefers orch builder names when running a compilator's compile.
  orch_name = chromium_api.m.properties.get('orchestrator', {}).get(
    'builder_name'
  )
  builder_name = orch_name if orch_name else builder_id.builder
  # Include instructions with no targets to compile all. This can cause
  # the instruction to reproduce failures in compile targets that are being
  # filtered on the builder. This is preferable to plumbing the test names
  # through compile functions for now
  utr_instructions = get_utr_instruction(
    'compile',
    chromium_api.m.buildbucket.build.builder.project,
    (
      chromium_api.m.led.shadowed_bucket
      or chromium_api.m.buildbucket.build.builder.bucket
    ),
    builder_name,
    [],
  )
  utr_instructions += '<br/>To run in your own build dir:<br/>'
  utr_instructions += get_utr_instruction(
    'compile',
    chromium_api.m.buildbucket.build.builder.project,
    (
      chromium_api.m.led.shadowed_bucket
      or chromium_api.m.buildbucket.build.builder.bucket
    ),
    builder_name,
    [],
    utr_flags=['--build-dir', '${YOUR_BUILD_DIR_HERE}'],
    include_preface_text=False,
  )
  local_instructions = (
    utr_instructions + '<br/>*To force non-remote services '
    'append --no-rbe and --no-siso, this will dramatically slow the build*'
  )
  dependency = chromium_api.m.repro_instructions.get_dependency(r'.*bot_update')
  chromium_api.m.repro_instructions.add_step_instruction(
    step_result,
    remote_content=utr_instructions,
    remote_dependency=dependency,
    local_content=local_instructions,
    local_dependency=dependency,
  )
  step_result.presentation.step_text += utr_instructions.replace('<br/>', '\n')
