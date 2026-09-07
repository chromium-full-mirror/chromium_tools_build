# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, StepCommandContains
from recipe_engine.recipe_api import Property

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import gn
from RECIPE_MODULES.recipe_engine import path, properties


@dataclass
class DEPS(RecipeScriptApi):
  gn: gn.API
  path: path.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API

PROPERTIES = {
    'args': Property(kind=str, default=None),
    'write_ide_json_filename': Property(kind=str, default=None),
}


def RunSteps(api: DEPS, args, write_ide_json_filename):
  api.gn.gen(
      api.path.cache_dir / 'builder' / 'src' / 'out' / 'Release',
      step_name='foobar',
      args=args,
      write_ide_json_filename=write_ide_json_filename)


def GenTests(api: TEST_DEPS):
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
          '--args=is_debug=false is_component_build=false',
          '[CACHE]/builder/src/out/Release',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'with json file output',
      api.properties(write_ide_json_filename='output.json'),
      api.post_process(StepCommandContains, 'foobar', [
          'RECIPE_REPO[depot_tools]/gn.py',
          'gen',
          '--ide=json',
          '--json-file-name=output.json',
          '[CACHE]/builder/src/out/Release',
      ]),
      api.post_process(DropExpectation),
  )
