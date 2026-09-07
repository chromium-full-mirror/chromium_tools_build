# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import gn
from RECIPE_MODULES.recipe_engine import path, properties, raw_io


@dataclass
class DEPS(RecipeScriptApi):
  gn: gn.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  gn: gn.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API

_DEFAULT_ARGS = ('target_cpu = "x86"\n'
                 'target_sysroot = "/sysroot/"\n')

def _test_args(api, args=None):
  args = args or _DEFAULT_ARGS
  return (api.properties(expected_args=args)
          + api.step_data('read GN args', api.raw_io.output_text(args)))


def RunSteps(api: DEPS):
  args = api.gn.get_args(
      api.path.cache_dir / 'builder' / 'src' / 'out' / 'Release',
      location=api.properties.get('location'),
      max_text_lines=api.properties.get('max_text_lines'))
  assert args == api.properties.get('expected_args'), \
      'expected:\n%s\nactual:%s' % (api.properties.get('expected_args'), args)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      _test_args(api),
      api.post_process(post_process.StepTextContains, 'read GN args',
                       ['target_cpu = "x86"']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'present_to_logs',
      _test_args(api),
      api.properties(location=api.gn.LOGS),
      api.post_process(post_process.LogContains, 'read GN args', 'gn_args', [
          'target_cpu = "x86"',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  args = '\n'.join('arg%02d = "value%d"' % (i, i) for i in range(10))
  yield api.test(
      'many_args',
      _test_args(api, args),
      api.properties(max_text_lines=5),
      api.post_process(post_process.LogContains, 'read GN args', 'gn_args',
                       [args]),
      api.post_process(post_process.StepTextContains, 'read GN args',
                       ['exceeds limit', 'presented in logs instead']),
      api.post_process(post_process.DropExpectation),
  )

  args = '\n'.join('arg%02d = "value%d"' % (i, i) for i in range(10))
  yield api.test(
      'present_to_text',
      _test_args(api, args),
      api.properties(location=api.gn.TEXT, max_text_lines=5),
      api.post_process(post_process.StepTextContains, 'read GN args',
                       [args.replace('\n', '<br/>')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'args_with_imports',
      _test_args(api, ('import("//build/args/headless.gn")\n'
                       'target_cpu = "x86"\n')),
      api.post_process(post_process.StepTextContains, 'read GN args',
                       [('import("//build/args/headless.gn")<br/>'
                         'target_cpu = "x86"')]),
      api.post_process(post_process.DropExpectation),
  )
