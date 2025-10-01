# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_types


DEPS = [
    'chromium',
    'recipe_engine/properties',
]


_BUILDERS_DICT = {
    'test_group': {
        'builders': {
            'test_buildername':
                chromium_types.BuilderSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['mb'],
                    gclient_apply_config=['android'],
                ),
        },
    },
    'tryserver_test': {
        'builders': {
            'mac_trybot':
                chromium_types.BuilderSpec.create(
                    chromium_config='chromium_clang',),
            'win_trybot':
                chromium_types.BuilderSpec.create(
                    chromium_config='chromium_clang',),
        },
    },
}


def RunSteps(api):
  api.chromium.configure_bot(_BUILDERS_DICT, additional_configs=['codesearch'])


def GenTests(api):
  for test in api.chromium.gen_tests_for_builders(_BUILDERS_DICT):
    yield test + api.post_process(post_process.DropExpectation)
