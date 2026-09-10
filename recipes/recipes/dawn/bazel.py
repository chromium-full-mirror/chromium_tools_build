# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe used by standalone Dawn CI builders which use Bazel"""

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.depot_tools import bot_update, gclient, osx_sdk
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  path,
  platform,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  context: context.API
  gclient: gclient.API
  osx_sdk: osx_sdk.API
  path: path.API
  platform: platform.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  # Set up gclient to checkout Dawn.
  api.gclient.set_config('dawn')

  # Configure gclient to fetch Dawn, bazel and CMake (clang toolchain).
  api.gclient.c.solutions[0].custom_vars = {
    'dawn_root': 'dawn',
    'fetch_bazel': 'True',
    'fetch_cmake': 'True',
  }

  # Ensure checkout and run hooks.
  cache_dir = api.path.cache_dir / 'builder'
  with api.context(cwd=cache_dir):
    update_result = api.bot_update.ensure_checkout()
    api.gclient.runhooks()

  source_dir = update_result.source_root.path
  bazelisk = source_dir.joinpath('tools', 'bazelisk', 'bazelisk')

  debug = api.properties.get('debug', True)
  compilation_mode = 'dbg' if debug else 'opt'

  # Build Tint using tools/bazelisk.
  bazel_args = [
    bazelisk,
    'build',
    '--compilation_mode=%s' % compilation_mode,
  ]

  # On Mac, use minimum necessary version that will enable C++20 APIs.
  if api.platform.is_mac:
    bazel_args.append('--macos_minimum_os=12.0')

  # Build all bazel targets.
  bazel_args.extend(
    [
      '//src/tint/...',
      '//src/utils/...',
      '//test/tint/...',
      '//:includes',
    ]
  )

  # On Linux, enforce using the hermetic Clang compiler.
  env = {}
  if api.platform.is_linux:
    clang_path = source_dir.joinpath(
      'third_party', 'llvm-build', 'Release+Asserts', 'bin', 'clang'
    )
    clang_xx_path = source_dir.joinpath(
      'third_party', 'llvm-build', 'Release+Asserts', 'bin', 'clang++'
    )
    env['CC'] = str(clang_path)
    env['CXX'] = str(clang_xx_path)

  with api.osx_sdk('mac'), api.context(cwd=source_dir, env=env):
    # Always expunge state first to avoid stale toolchain cache issues
    # (crbug.com/530610023).
    api.step('bazel clean', [bazelisk, 'clean', '--expunge'])
    api.step('bazel build all', bazel_args)


def GenTests(api: TEST_DEPS):
  yield api.test(
    'linux_rel',
    api.platform('linux', 64),
    api.buildbucket.ci_build(
      project='dawn',
      builder='dawn-linux-x64-bazel-rel',
      git_repo='https://dawn.googlesource.com/dawn',
    ),
    api.properties(debug=False),
    api.post_process(post_process.StepSuccess, 'bazel build all'),
  )
  yield api.test(
    'linux_dbg',
    api.platform('linux', 64),
    api.buildbucket.ci_build(
      project='dawn',
      builder='dawn-linux-x64-bazel-dbg',
      git_repo='https://dawn.googlesource.com/dawn',
    ),
    api.properties(debug=True),
    api.post_process(post_process.StepSuccess, 'bazel build all'),
  )
  yield api.test(
    'mac_rel',
    api.platform('mac', 64),
    api.buildbucket.ci_build(
      project='dawn',
      builder='dawn-mac-arm64-bazel-rel',
      git_repo='https://dawn.googlesource.com/dawn',
    ),
    api.properties(debug=False),
    api.post_process(post_process.StepSuccess, 'bazel build all'),
  )
