# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import contextlib

from google.protobuf import json_format
from recipe_engine import recipe_api, turboci

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2
from PB.turboci.graph.orchestrator.v1.query import Query

BUILD_CANCELED_SUMMARY = 'Build was canceled.'


class ChromiumTurbociApi(recipe_api.RecipeApi):

  @contextlib.contextmanager
  def display_turboci_checks(self):
    try:
      yield
    finally:
      if (not self.m.runtime.in_global_shutdown and
          'luci.buildbucket.run_in_turboci'
          in self.m.buildbucket.build.input.experiments):
        # Output all checks from the workplan when running in turboci, mainly for
        # inspection purpose.
        # TODO(https://crbug.com/493256377): Remove this step when we have a
        # better place to show checks
        step_result = self.m.step.empty('TurboCI Checks')
        # Use the same turboci query as that in
        # recipes-py/recipe_engine/internal/test/magic_check_fn.py
        workplan = turboci.query_nodes(
            turboci.make_query(
                Query.SelectChecks(),
                Query.CollectChecks(
                    options=True,
                    result_data=True,
                ),
            ),
            types=('*',)).workplans[0]
        step_result.presentation.logs['checks'] = json_format.MessageToJson(
            workplan)
