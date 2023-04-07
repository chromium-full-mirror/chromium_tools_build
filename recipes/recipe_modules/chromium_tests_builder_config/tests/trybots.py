# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Test to ensure the validity of the entries within TRYBOTS.

Each entry in the TRYBOTS dict will be checked to ensure
chromium_tests.trybot_steps can be called with the input set as it would
be when the try builder runs.
"""

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium_tests',
    'chromium_tests_builder_config',
    'filter',
    'recipe_engine/properties',
    'recipe_engine/step',
]


def RunSteps(api):
  builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder())
  api.chromium_tests.trybot_steps(builder_id, builder_config)
  api.step('Success', ['echo', 'Success!'])


def GenTests(api):
  for builder_id in sorted(ctbc.trybots.TRYBOTS):
    builder_group = builder_id.group
    buildername = builder_id.builder
    yield api.test(
        ('%s-%s' % (builder_group, buildername)).replace(' ', '_'),
        (api.properties(xcode_build_version='11c29')
         if 'ios' in buildername else api.properties()),
        api.chromium_tests_builder_config.try_build(
            builder_group=builder_group,
            builder=buildername,
            patch_set=1,
        ),
        # Supress analysis so that all targets show up as affected and we run
        # recipe code for each configured test
        api.post_process(post_process.DropExpectation),
    )
