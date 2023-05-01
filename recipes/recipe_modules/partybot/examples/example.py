# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Example use of the PartyBot recipe module and tests."""

from recipe_engine import post_process
from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

DEPS = [
    'partybot',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/raw_io',
]


def RunSteps(api):
  api.partybot.run("chromium", api.json.output(name='deps'),
                   "project.dataset.table")


def GenTests(api):

  build = build_pb2.Build(
      infra=build_pb2.BuildInfra(
          swarming=build_pb2.BuildInfra.Swarming(task_dimensions=[
              common_pb2.RequestedDimension(key="os", value="linux")
          ]),))

  yield api.test(
      'basic',
      api.buildbucket.build(build),
  )

  yield api.test(
      'failure_no_bot_os',
      api.buildbucket.generic_build(),
      api.step_data(
          'get_bot_os',
          api.raw_io.stream_output_text(
              'Unable to get bot OS from swarming task dimensions.')),
      status='FAILURE',
  )
