# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import recipe_api
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb


class LuciBisectionApi(recipe_api.RecipeApi):
  def send_result_to_luci_bisection(self, step_name, analysis_id, result, host):
    bbid = str(self.m.buildbucket.build.id)
    bot_id = self.m.swarming.bot_id
    # Leave the test data here for the purpose of led testing, because led build has no bbid
    # bbid = "8804856879691073905"
    # analysis_id = "5655880053817344"

    method = "luci.bisection.v1.BotUpdates.UpdateAnalysisProgress"
    request_input = {
      "analysisId": analysis_id,
      "bbid": bbid,
      "botId": bot_id,
      "gitilesCommit": {
        "host": self.m.buildbucket.gitiles_commit.host,
        "project": self.m.buildbucket.gitiles_commit.project,
        "id": self.m.buildbucket.gitiles_commit.id,
        "ref": self.m.buildbucket.gitiles_commit.ref,
      },
      "rerunResult": {"rerunStatus": self.rerun_result(result)},
    }
    self._call_prpc(step_name, host, method, request_input)

  def send_test_results_to_luci_bisection(
    self, step_name, test_results, run_succeeded, host
  ):
    bbid = str(self.m.buildbucket.build.id)
    bot_id = self.m.swarming.bot_id
    # Leave the test data here for the purpose of led testing, because led build has no bbid
    # bbid = "8804856879691073905"

    method = "luci.bisection.v1.BotUpdates.UpdateTestAnalysisProgress"
    request_input = {
      "bbid": bbid,
      "botId": bot_id,
      "results": self._rerun_test_results(test_results),
      "runSucceeded": run_succeeded,
    }
    self._call_prpc(step_name, host, method, request_input)

  def _call_prpc(self, step_name, host, method, request_input):
    args = [
      'prpc',
      'call',
      host,
      method,
    ]
    result = self.m.step(
      step_name,
      args,
      stdin=self.m.json.input(request_input),
      stdout=self.m.json.output(add_json_log=True),
    )
    result.presentation.logs['input'] = self.m.json.dumps(
      request_input, indent=2
    )
    return result.stdout

  def _rerun_test_results(self, test_results):
    res = []
    for r in test_results:
      res.append(
        {
          "testId": r.test_id,
          "variantHash": r.variant_hash,
          "isExpected": r.expected,
          "status": r.status,
        }
      )
    return res

  def rerun_result(self, result):
    if result == common_pb.SUCCESS:
      return "RERUN_STATUS_PASSED"
    if result == common_pb.FAILURE:
      return "RERUN_STATUS_FAILED"
    if result == common_pb.INFRA_FAILURE:
      return "RERUN_STATUS_INFRA_FAILED"
    return "RERUN_STATUS_UNSPECIFIED"
