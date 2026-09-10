# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Exporting strategy related methods as CLI for recipe scripts."""

from __future__ import annotations

import sys
import typing

from api_runner_common import main
from libs.result_summary import BaseResultSummary
from libs.test_binary import BaseTestBinary
from libs.strategies import strategies, ReproducingStep


def choose_strategies(
  test_binary: BaseTestBinary, result_summary: BaseResultSummary, test_name: str
):
  """Chooses the strategies that be applied to the test.

  Return:
    A list of Strategy names that can be applied to the test.
  """
  chosen_strategies = []
  for strategy_cls in strategies.values():
    strategy = strategy_cls(test_binary, result_summary, test_name)
    if strategy.valid_for_test():
      chosen_strategies.append(strategy)
  return [s.name for s in chosen_strategies]


def choose_best_reproducing_step(
  reproducing_steps: typing.List[ReproducingStep],
):
  """Chooses the best ReproducingStep produced by the strategies."""
  best_step = None
  for step in reproducing_steps:
    if not best_step or step.better_than(best_step):
      best_step = step
  if best_step:
    return best_step.to_jsonish()


def count_reproduced_failures(
  test_name: str,
  result_summaries: typing.List[BaseResultSummary],
  original_result_summary: BaseResultSummary,
):
  failing_sample = original_result_summary.get_failing_sample(test_name)
  cnt = 0
  for result_summary in result_summaries:
    for result in result_summary.get_all(test_name):
      if failing_sample.similar_with(result):
        cnt += 1
        break
  return cnt


def summarize_reproducing_steps(
  reproducing_step: ReproducingStep,
  all_reproducing_steps: typing.List[ReproducingStep],
):
  """Format reproducing_steps as human readable message."""
  summary_header = ''
  summary = []

  # reproduce info
  summary.append('\n')
  if reproducing_step:
    readable_info = reproducing_step.readable_info()
    summary_header = readable_info.strip().split('\n')[0]
    summary.append(readable_info)
  else:
    summary_header = 'The failure could NOT be reproduced.'
    summary.append(summary_header)

  # strategies info
  if all_reproducing_steps:
    # Adding tailing '  ' to force line break for markdown.
    summary.append("\nIt's verified with following strategies:  ")
    for step in all_reproducing_steps:
      if step.debug_info.get('task_ui_link'):
        message = "[{0} strategy]({1})".format(
          step.strategy, step.debug_info['task_ui_link']
        )
      else:
        message = "{0} strategy".format(step.strategy)
      if step.reproduced_cnt:
        message += " reproduced {0} times ({1:.1f}%)".format(
          step.reproduced_cnt, step.reproducing_rate * 100
        )
      else:
        message += " not reproduced"
      # Adding tailing '  ' to force line break for markdown.
      summary.append(message + '  ')

  return summary_header, '\n'.join(summary)


methods = {
  'choose_strategies': choose_strategies,
  'choose_best_reproducing_step': choose_best_reproducing_step,
  'count_reproduced_failures': count_reproduced_failures,
  'summarize_reproducing_steps': summarize_reproducing_steps,
}

if __name__ == '__main__':
  main(sys.argv[1:], methods)  # pragma: no cover
