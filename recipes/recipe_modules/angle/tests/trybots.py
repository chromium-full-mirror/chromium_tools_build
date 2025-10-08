# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test to ensure the validity of the entries within TRYBOTS.

Each entry in the TRYBOTS dict will be checked to ensure
chromium_tests.trybot_steps can be called with the input set as it would
be when the try builder runs.

Copied from recipe_modules/chromium_tests/tests/trybots.py.
"""

from __future__ import annotations

from recipe_engine import post_process

from RECIPE_MODULES.build.angle import trybots as angle_trybots
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'angle',
    'chromium',
    'chromium_tests',
    'filter',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api):
  api.angle.steps()
  api.step('Success', ['echo', 'Success!'])


def GenTests(api):
  for builder_id in sorted(angle_trybots.TRYBOTS):
    builder_group = builder_id.group
    buildername = builder_id.builder
    yield api.test(
        ('%s-%s' % (builder_group, buildername)).replace(' ', '_'),
        api.properties(test_mode='compile_and_test'),
        api.angle.try_build(
            builder=buildername,
            patch_set=1,
        ),
        api.post_process(post_process.DropExpectation),
        api.angle.override_commit_pos_data(),
    )
