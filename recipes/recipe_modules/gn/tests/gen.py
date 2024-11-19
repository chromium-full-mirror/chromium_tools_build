# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, StepCommandContains
from recipe_engine.recipe_api import Property

DEPS = [
    'recipe_engine/path',
    'recipe_engine/properties',
    'gn',
]

PROPERTIES = {
    'args': Property(kind=str, default=None),
}


def RunSteps(api, args):
  api.gn.gen(
      api.path.cache_dir / 'builder' / 'src' / 'out' / 'Release',
      step_name='foobar',
      args=args)


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(StepCommandContains, 'foobar', [
          'RECIPE_REPO[depot_tools]/gn.py',
          'gen',
          '[CACHE]/builder/src/out/Release',
      ]),
      api.post_process(DropExpectation),
  )
  yield api.test(
      'with args',
      api.properties(args='is_debug=false is_component_build=false'),
      api.post_process(StepCommandContains, 'foobar', [
          'RECIPE_REPO[depot_tools]/gn.py',
          'gen',
          '--args',
          'is_debug=false is_component_build=false',
          '[CACHE]/builder/src/out/Release',
      ]),
      api.post_process(DropExpectation),
  )
