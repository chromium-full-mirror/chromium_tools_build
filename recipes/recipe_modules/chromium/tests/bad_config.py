# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.config import BadConf

from RECIPE_MODULES.build.chromium.config import config_ctx

DEPS = [
    'chromium',
    'recipe_engine/assertions',
]


@config_ctx()
def bad_generator(c):
  c.project_generator.tool = 'this is not a valid generator'


def RunSteps(api):
  with api.assertions.assertRaises(BadConf):
    api.chromium.set_config('bad_generator')


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
