# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from . import commons
from . import test262_recovery

from recipe_engine import recipe_api

from PB.go.chromium.org.luci.buildbucket.proto.common import FAILURE
from PB.recipe_engine.result import RawResult


class V8RollWatcherApi(recipe_api.RecipeApi):
  def process_rollers(self, roller_configs):
    rollers = [commons.Roller(r, self.m) for r in roller_configs]
    with self.m.depot_tools.on_path():
      for roller in rollers:
        with self.m.step.nest(f"Roller: '{roller.name}'"):
          self.process_roller(roller)
    if any(roller.has_failure for roller in rollers):
      return RawResult(status=FAILURE)

  def process_roller(self, roller):
    open_cls = roller.find_open_cls()
    for cl in open_cls:
      process_cl(self.m, roller, cl)


def process_cl(api, roller, cl):
  with api.step.nest(f'Checking CL {cl.number}') as parent_step:
    parent_step.links.update(cl.presentation_links)
    if cl.has_blocking_failures():
      run_failure_recovery(api, roller, cl)
    else:
      api.step('No CQ failures on this CL yet...', [])


def run_failure_recovery(api, roller, cl):
  for recovery_fn_name in roller.failure_recovery:
    recovery_fn = find_recovery_fn(recovery_fn_name)
    recovery_fn(api, roller, cl)


def find_recovery_fn(name):
  return {
    'just_fail': commons.just_fail,
    'just_pass': commons.just_pass,
    'mark_as_reported': commons.mark_as_reported,
    'test262_update_status_file': test262_recovery.test262_update_status_file,
  }[name]
