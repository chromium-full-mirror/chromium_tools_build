# Copyright (c) 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to enforce that testing occurs for CLs that require testing.

This recipe fails for any CL that has the Requires-Testing footer set in
the CL description. A builder running this recipe can be set up to only
run in a fallback CQ group. The builder will only run for branches which
an actual CQ is not running, so automated CLs that are created by
rollers that set the Requires-Testing footer won't be able to land if
the branch has not been configured.
"""

from recipe_engine import post_process

from PB.recipe_engine import result as result_pb
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

DEPS = [
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/step',
]

_FOOTER = 'Requires-Testing'


def RunSteps(api):
  assert api.tryserver.is_tryserver
  if api.tryserver.get_footer('Requires-Testing'):
    return result_pb.RawResult(
        status=common_pb.FAILURE,
        summary_markdown=(
            "The CL requires testing ('{}' footer is set in description)"
            ' and CQ is not enabled for this branch'.format(_FOOTER)))


def GenTests(api):
  yield api.test(
      'requires-testing',
      api.buildbucket.try_build(),
      api.override_step_data('parse description',
                             api.json.output({_FOOTER: 'true'})),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'does-not-require-testing',
      api.buildbucket.try_build(),
      api.post_process(post_process.DropExpectation),
  )
