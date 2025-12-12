# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from contextlib import contextmanager
from recipe_engine.recipe_api import Property

DEPS = [
    'chromium',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/osx_sdk',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/step',
]


@contextmanager
def _BazelShutdown(api, bazel):
  try:
    yield
  finally:
    api.step('bazel shutdown', [bazel, 'shutdown'], ok_ret='any')


def RunSteps(api):
  # Print the kernel version on Linux builders. BoringSSL is sensitive to
  # whether the kernel has getrandom support.
  if api.platform.is_linux:
    api.step('uname', ['uname', '-a'])

  # Sync and pull in everything.
  api.gclient.set_config('boringssl')
  api.gclient.c.solutions[0].custom_vars = {
      'checkout_bazel': True,
      # No need for CMake and Ninja.
      'checkout_cmake': False,
      # Go and Perl are only needed if running util/pregenerate.
      'checkout_go': False,
      'checkout_perl': False,
  }
  cache_dir = api.path.cache_dir / 'builder'
  with api.context(cwd=cache_dir):
    update_result = api.bot_update.ensure_checkout()
    api.gclient.runhooks()

  # TODO(davidben): Add Windows support. The main challenge will be helping
  # Bazel find the depot_tools copy of MSVC. See
  # https://bazel.build/configure/windows#build_cpp

  with api.osx_sdk('ios'):
    src = update_result.source_root.path
    bazel = src / 'util/bot/bazel/bazel'
    with api.context(cwd=src), _BazelShutdown(api, bazel):
      api.step('bazel build', [
          bazel, 'build', '--verbose_failures', '--lockfile_mode=error', '...'
      ])
      api.step('bazel test', [
          bazel, 'test', '--verbose_failures', '--lockfile_mode=error',
          '--test_output=errors', '...'
      ])

    bazel_example = src / "util/bazel-example"
    with api.context(cwd=bazel_example), _BazelShutdown(api, bazel):
      api.step('bazel build example', [
          bazel, 'build', '--verbose_failures', '--lockfile_mode=error', '...'
      ])
      # The example consumer has no tests. Just make sure it builds.

def _CIBuild(api, builder):
  return api.buildbucket.ci_build(
      project='boringssl',
      builder=builder,
      git_repo='https://boringssl.googlesource.com/boringssl')


def GenTests(api):
  tests = [
      ('linux', api.platform('linux', 64)),
      ('mac', api.platform('mac', 64)),
  ]
  for (buildername, host_platform) in tests:
    yield api.test(
        buildername,
        host_platform,
        _CIBuild(api, buildername),
    )

  yield api.test(
      'bazel_build_failed',
      api.platform('linux', 64),
      _CIBuild(api, 'linux'),
      api.override_step_data('bazel build', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'bazel_test_failed',
      api.platform('linux', 64),
      _CIBuild(api, 'linux'),
      api.override_step_data('bazel test', retcode=1),
      api.expect_status('FAILURE'),
  )
