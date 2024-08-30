# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Creates the instruction to run UTR."""
from collections.abc import Iterable


def get_utr_instruction(command: str,
                        project: str,
                        bucket: str,
                        builder: str,
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

  def sanitize_arg(s):
    s = s.replace('"', '\\"').replace("'", "\\'")
    if len(s.split()) > 1:
      return '"' + s + '"'
    return s

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
  if not test_names:
    test_names = []
  for test in test_names:
    utr_cmd.extend(['-t', test])
  utr_cmd.append(command)

  if extra_args:
    utr_cmd.extend(extra_args)

  utr_cmd = ' '.join([sanitize_arg(arg) for arg in utr_cmd])
  utr_readme_url = 'https://chromium.googlesource.com/chromium/src/+/main/tools/utr/README.md'
  lines = []
  lines.append(
      f'[UTR]({utr_readme_url}) command to reproduce from your Chromium '
      'checkout:')
  lines.append('```' + utr_cmd + '```')
  lines.append('')
  return '<br/>'.join(lines)
