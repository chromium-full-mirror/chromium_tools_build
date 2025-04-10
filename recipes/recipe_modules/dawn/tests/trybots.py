# Copyright 2025 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests to ensure the validity of the entries within trybots.py.

Each entry will be checked to ensure dawn.try_steps can be called with the
input set as it would be when the try builder runs.
"""

from recipe_engine import post_process

from RECIPE_MODULES.build.dawn import trybots as dawn_trybots

DEPS = [
    'chromium',
    'dawn',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api):
  api.dawn.try_steps()
  api.step('Success', ['echo', 'Success!'])


def GenTests(api):
  for builder_id in sorted(dawn_trybots.TRYBOTS):
    builder_group = builder_id.group
    buildername = builder_id.builder
    yield api.test(
        ('%s-%s' % (builder_group, buildername)).replace(' ', '_'),
        api.chromium.try_build(
            builder_group=builder_group,
            builder=buildername,
            project='dawn',
            git_repo='https://dawn.googlesource.com/dawn/'),
        api.post_process(post_process.DropExpectation),
    )
