# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for building V8 with bazel.
"""

from recipe_engine.post_process import Filter

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, v8
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.recipe_engine import (
    context,
    file,
    path,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  context: context.API
  file: file.API
  gclient: gclient.API
  path: path.API
  properties: properties.API
  step: step.API
  v8: v8.API


def RunSteps(api: DEPS):
  api.gclient.set_config('v8_with_bazel')
  api.chromium.set_config('v8')
  update_result = api.v8.checkout()
  source_dir = update_result.source_root.path
  build_dir = api.v8.build_dir(source_dir)
  api.v8.runhooks(source_dir, build_dir)

  bazel = source_dir.joinpath('tools', 'bazel', 'bazel')
  clang = source_dir.joinpath('third_party', 'llvm-build', 'Release+Asserts',
                              'bin')
  clang_bin = clang / 'clang'
  clang_xx_bin = clang / 'clang++'
  with api.context(
      cwd=source_dir,
      env={
          'BAZEL_COMPILER': 'clang',
          'CC': clang_bin,
          'CXX': clang_xx_bin
      },
      env_prefixes={'PATH': [clang, api.v8.depot_tools_path(source_dir)]}):

    # TODO(https://crbug.com/v8/13515): Temporarily clobber the output
    # directory to avoid incremental build problems.
    api.file.rmtree('Clobber bin dir', source_dir / 'bazel-bin')
    api.file.rmtree('Clobber out dir', source_dir / 'bazel-out')
    api.file.rmtree('Clobber bazel cache',
                    api.path.home_dir.joinpath('.cache', 'bazel'))

    try:
      api.step(
          'Bazel build',
          [bazel, 'build', '--verbose_failures', ':v8ci'])
    finally:
      api.step('Bazel shutdown', [bazel, 'shutdown'])


def GenTests(api: RecipeTestApi):
  yield api.test(
      'basic',
      api.post_process(Filter(
          'Bazel build', 'Bazel shutdown', 'Clobber bazel cache')),
      status='SUCCESS',
  )
