# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.recipe_api import InfraFailure, StepFailure

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb


class Results:
  def __init__(
    self, infra_failures=None, task_failures=None, exonerated_failures=None
  ):
    self.infra_failures = infra_failures or []
    self.task_failures = task_failures or []
    self.exonerated_failures = exonerated_failures or []

  def __add__(self, result):
    return Results(
      self.infra_failures + result.infra_failures,
      self.task_failures + result.task_failures,
      self.exonerated_failures + result.exonerated_failures,
    )

  def exonerable(self):
    """True if the result contains only test failures.
    Infra failures are not exonerable."""
    return bool(self.task_failures and not self.infra_failures)

  def can_exonerate(self):
    """True if this result can exonerate other results."""
    return not bool(self.task_failures or self.infra_failures)

  def add_infra_failure(self, failure):
    self.infra_failures.append(failure)

  def add_test_failure(self, failure):
    self.task_failures.append(failure)

  def raise_on_failure(self):
    """
    Prioritize test failures in order to be able to close the tree even if we
    have infra failures.
    """
    if self.task_failures:
      raise StepFailure(', '.join(self.task_failures))

    if self.infra_failures:
      raise InfraFailure(', '.join(self.infra_failures))

  def raw_result(self):
    self.raise_on_failure()
    summary = None
    if self.exonerated_failures:
      summary = 'Flaky tests exonerated: ' + ', '.join(self.exonerated_failures)
    return result_pb2.RawResult(
      summary_markdown=summary,
      status=common_pb.SUCCESS,
    )


class SwarmingTrigger:
  def __init__(self, api, cas_digest, target_dimensions=None):
    self.api = api
    self.cas_digest = cas_digest
    self.target_dimensions = target_dimensions

  def trigger(
    self, step_name, output_dir, test_type_tag, run_phase, commands, env
  ):
    return self.api.devtools.trigger_test_swarming_tasks(
      step_name=step_name,
      cas_digest=self.cas_digest,
      task_output_dir=output_dir,
      rdb_test_type=test_type_tag,
      run_phase=run_phase,
      commands=commands,
      env=env,
      target_dimensions=self.target_dimensions,
    )
