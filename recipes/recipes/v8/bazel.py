# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe for building V8 with bazel.
"""

from recipe_engine.post_process import Filter

DEPS = [
  'chromium',
  'depot_tools/gclient',
  'recipe_engine/context',
  'recipe_engine/file',
  'recipe_engine/path',
  'recipe_engine/properties',
  'recipe_engine/step',
  'v8',
]


def RunSteps(api):
  api.gclient.set_config('v8_with_bazel')
  api.chromium.set_config('v8')
  api.v8.checkout()
  api.v8.runhooks()
  bazel = api.path['checkout'].join('tools', 'bazel', 'bazel')
  clang = api.path['checkout'].join(
      'third_party', 'llvm-build', 'Release+Asserts', 'bin')
  clang_bin = clang.join('clang')
  clang_xx_bin = clang.join('clang++')
  with api.context(
      cwd=api.path['checkout'],
      env={'BAZEL_COMPILER': 'clang', 'CC': clang_bin, 'CXX': clang_xx_bin},
      env_prefixes={'PATH': [clang, api.v8.depot_tools_path]}):

    # TODO(https://crbug.com/v8/13515): Temporarily clobber the output
    # directory to avoid incremental build problems.
    api.file.rmtree('Clobber bin dir', api.path['checkout'].join('bazel-bin'))
    api.file.rmtree('Clobber out dir', api.path['checkout'].join('bazel-out'))
    api.file.rmtree(
        'Clobber bazel cache', api.path['home'].join('.cache', 'bazel'))

    try:
      api.step(
          'Bazel build',
          [bazel, 'build', '--verbose_failures', ':v8ci'])
    finally:
      api.step('Bazel shutdown', [bazel, 'shutdown'])


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(Filter(
          'Bazel build', 'Bazel shutdown', 'Clobber bazel cache')),
      status='SUCCESS',
  )
