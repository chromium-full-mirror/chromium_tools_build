# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
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
    'label_or_pattern': Property(kind=str, default='target1'),
    'what_to_show': Property(kind=str, default='deps'),
    'use_cache': Property(kind=bool, default=False),
    'all_flag': Property(kind=bool, default=False),
}


def RunSteps(api: DEPS, label_or_pattern, what_to_show, use_cache, all_flag):
  build_dir = api.path.cache_dir / 'builder' / 'src' / 'out' / 'Release'
  flags = []
  if all_flag:
    flags.append('--all')

  # First call
  res1 = api.gn.desc(
      build_dir,
      label_or_pattern,
      what_to_show,
      *flags,
      step_name='desc 1',
      use_cache=use_cache)

  # Second call with same parameters
  res2 = api.gn.desc(
      build_dir,
      label_or_pattern,
      what_to_show,
      *flags,
      step_name='desc 2',
      use_cache=use_cache)

  api.assertions.assertEqual(res1, ['file1', 'file2'])
  api.assertions.assertEqual(res2, ['file1', 'file2'])


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.properties(
          label_or_pattern='target1',
          what_to_show='runtime_deps',
          use_cache=False,
      ),
      api.override_step_data(
          'desc 1', stdout=api.raw_io.output_text('file1\nfile2')),
      api.override_step_data(
          'desc 2', stdout=api.raw_io.output_text('file1\nfile2')),
      api.post_process(post_process.StepSuccess, 'desc 1'),
      api.post_process(post_process.StepSuccess, 'desc 2'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cache_hit',
      api.properties(
          label_or_pattern='target1',
          what_to_show='deps',
          all_flag=True,
          use_cache=True,
      ),
      api.override_step_data(
          'desc 1', stdout=api.raw_io.output_text('file1\nfile2')),
      # desc 2 should NOT be called because it's a cache hit
      api.post_process(post_process.StepSuccess, 'desc 1'),
      api.post_process(post_process.DoesNotRun, 'desc 2'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cache_bypass',
      api.properties(
          label_or_pattern='target1',
          what_to_show='deps',
          all_flag=True,
          use_cache=False,
      ),
      api.override_step_data(
          'desc 1', stdout=api.raw_io.output_text('file1\nfile2')),
      api.override_step_data(
          'desc 2', stdout=api.raw_io.output_text('file1\nfile2')),
      api.post_process(post_process.StepSuccess, 'desc 1'),
      api.post_process(post_process.StepSuccess, 'desc 2'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cache_hit_runtime_deps',
      api.properties(
          label_or_pattern='target1',
          what_to_show='runtime_deps',
          all_flag=True,
          use_cache=True,
      ),
      api.override_step_data(
          'desc 1', stdout=api.raw_io.output_text('file1\nfile2')),
      # desc 2 should NOT be called because it's a cache hit
      api.post_process(post_process.StepSuccess, 'desc 1'),
      api.post_process(post_process.DoesNotRun, 'desc 2'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'not_cacheable_no_all',
      api.properties(
          label_or_pattern='target1',
          what_to_show='deps',
          all_flag=False,
          use_cache=True,
      ),
      api.override_step_data(
          'desc 1', stdout=api.raw_io.output_text('file1\nfile2')),
      api.override_step_data(
          'desc 2', stdout=api.raw_io.output_text('file1\nfile2')),
      api.post_process(post_process.StepSuccess, 'desc 1'),
      api.post_process(post_process.StepSuccess, 'desc 2'),
      api.post_process(post_process.DropExpectation),
  )
