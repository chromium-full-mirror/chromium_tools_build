# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Runs Crowbar workflows in chromium/src repos.

Checks out chromium/src, then find and trigger Crowbar workflows to vendor
third-party code into chromium/src, or to build and publish third-party code
as CIPD packages.

For more information, see:
https://chromium.googlesource.com/chromium/src/+/main/docs/managing-third-party/crowbar-workflow.md
"""

from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/gclient',
    'infra/crowbar',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]


def RunSteps(api):
  api.gclient.set_config('chromium')

  c = api.chromium_checkout.ensure_checkout()
  pkgs = api.crowbar.list_packages(c.checkout_dir)
  api.crowbar.build(c.checkout_dir, packages=pkgs)


def GenTests(api):
  yield api.test(
      'ci build',
      api.chromium.ci_build(
          builder_group='chromium.linux',
          builder='Linux Builder',
      ),
      api.step_data(
          'Find Crowbar Packages',
          api.raw_io.stream_output_text('foo/crowbar.txtpb\n', stream='stdout'),
      ),
      api.step_data(
          'build packages',
          api.step.sub_build(build_pb2.Build(status=common_pb.SUCCESS)),
      ),
      api.post_process(post_process.StepCommandContains, 'build packages',
                       ['foo']),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
