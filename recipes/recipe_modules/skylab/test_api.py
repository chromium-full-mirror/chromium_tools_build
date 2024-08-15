# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_test_api

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2


class SkylabTestApi(recipe_test_api.RecipeTestApi):
  """Test examples for Skylab api."""

  def mock_wait_on_suites(self, step_name, total_shards, runner_builds=None):
    """Emulates the step of wait_on_suites().

    Args:
    * step_name: The step name to overwrite the return.
    * total_shards: The number of buildbucket responses to mock.
    * runner_builds: The mock test runner builds kicked off by CTP, in the
        form of list[list[(build id, build status)]]. The outer list is for
        shards, and the inner list is for multilple attempts. Filled with
        all success attempts by default.

    Returns:
      A list of steps with mock response.
    """
    if runner_builds is None:
      runner_builds = [
          [(900 + i), common_pb2.SUCCESS] for i in range(total_shards)
      ]
    res = {}
    for i, shard_result in enumerate(runner_builds):
      if shard_result is None:  #pragma: no cover
        continue
      build_id, status = shard_result
      res[str(i)] = {
          'url': ('https://ci.chromium.org/p/chromeos/builders/test_runner/'
                  f'test_runner/b{build_id}'),
          'log_url':
              ('https://cros-test-analytics.appspot.com/p/chromeos/logs/'
               'browse/chromeos-test-logs/test-runner/prod/2023-12-07/abcd'),
          'status': common_pb2.Status.Name(status),
      }
    return sum([
        self.override_step_data(f'{step_name}.read_ctp_response',
                                self.m.json.output(res))
    ], self.empty_test_data())
