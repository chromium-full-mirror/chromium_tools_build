# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.config import List
from recipe_engine.recipe_api import Property

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import gn
from RECIPE_MODULES.recipe_engine import (
    assertions,
    path,
    properties,
    raw_io,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  gn: gn.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API

PROPERTIES = {
    'targets': Property(kind=List(str), default=[]),
    'output_type': Property(kind=str, default=None),
}


def RunSteps(api: DEPS, targets, output_type):
  refs = api.gn.refs(
      api.path.cache_dir / 'builder' / 'src' / 'out' / 'Release',
      targets,
      output_type=output_type)
  api.assertions.assertEqual(refs, set(['target3', 'target4']))


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.properties(targets=['target1', 'target2']),
      api.override_step_data(
          'calculate gn refs',
          stdout=api.raw_io.output_text('target3\ntarget4')),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_with_type',
      api.properties(
          targets=['target1', 'target2'],
          output_type='executable',
      ),
      api.override_step_data(
          'calculate gn refs',
          stdout=api.raw_io.output_text('target3\ntarget4')),
      api.post_process(post_process.DropExpectation),
  )
