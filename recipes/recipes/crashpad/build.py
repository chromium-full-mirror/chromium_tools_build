# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Buildbot recipe definition for the various Crashpad continuous builders.
"""

import ast
import contextlib

from recipe_engine.recipe_api import Property

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.depot_tools import (
    bot_update,
    depot_tools,
    gclient,
    osx_sdk,
    windows_sdk,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    file,
    json,
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
  depot_tools: depot_tools.API
  file: file.API
  gclient: gclient.API
  json: json.API
  osx_sdk: osx_sdk.API
  path: path.API
  platform: platform.API
  properties: properties.API
  step: step.API
  windows_sdk: windows_sdk.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  path: path.TEST_API
  properties: properties.TEST_API

PROPERTIES = {
    'config':
        Property(kind=str, help='Debug or Release', default='Debug'),
    'target_os':
        Property(
            kind=str, help='win, mac, ios, linux, or fuchsia', default=None),
    'target_cpu':
        Property(kind=str, help='x64, arm64, or ""', default=''),
}


def RunSteps(api: DEPS, config, target_os, target_cpu):
  """Generates the sequence of steps that will be run by the builder."""
  api.gclient.set_config('crashpad')

  env = {}

  is_fuchsia = is_ios = is_linux = is_mac = is_win = False
  if target_os == 'fuchsia':
    is_fuchsia = True
  elif target_os == 'ios':
    is_ios = True
  elif target_os == 'linux':
    is_linux = True
  elif target_os == 'mac':
    is_mac = True
  elif target_os == 'win':
    is_win = True

  assert is_fuchsia or is_ios or is_linux or is_mac or is_win

  @contextlib.contextmanager
  def sdk(os, arch='x64'):
    if os in ('ios', 'mac'):
      with api.osx_sdk(os):
        yield
    elif os == 'win':
      assert arch in ('x86', 'x64')
      with api.windows_sdk(target_arch=arch):
        yield
    else:
      yield

  api.gclient.c.target_os = {target_os}

  is_debug = config == 'Debug'

  if is_linux:
    # The Linux build uses the system compiler by default, but bots do not
    # have a clang installed by default, so use a local copy. Setting this
    # variable causes DEPS to download a clang cipd package in runhooks. See
    # also the setting of clang_path below.
    api.gclient.c.solutions[0].custom_vars['pull_linux_clang'] = True

  if is_ios:
    # Chrome on iOS checkout runs crashpad/build/ios/setup-ios-gn.py script
    # as part of "gclient runhooks". This script is not designed to run
    # on bots as its output is overwritten by the bots. Set a custom_vars
    # to disable the script.
    api.gclient.c.solutions[0].custom_vars['run_setup_ios_gn'] = False

  # Place the checkout inside the "builder" cache on iOS, to avoid conflicting
  # with existing Mac checkouts on the same machine.
  # TODO(crbug.com/crashpad/319): Use the builder cache on all platforms.
  if is_ios:
    with api.context(cwd=api.path.cache_dir / 'builder'):
      update_result = api.bot_update.ensure_checkout()
  else:
    update_result = api.bot_update.ensure_checkout()
  source_dir = update_result.source_root.path

  # buildbot sets 'clobber' to the empty string which is falsey, check with
  # 'in'
  if 'clobber' in api.properties:
    api.file.rmtree('out', source_dir / 'out')

  with api.context(env=env):
    api.gclient.runhooks()

  dirname = config
  if is_ios:
    if target_cpu == 'arm64':
      dirname += '-iphoneos'
    else:
      dirname += '-iphonesimulator'

  if is_fuchsia or is_ios or is_linux or is_mac:
    # TODO(crbug.com/crashpad/319): Simplify once all platforms use the cache.
    if is_ios:
      gn = api.path.cache_dir.joinpath('builder', 'buildtools', 'mac', 'gn')
    else:
      gn = api.path.start_dir.joinpath('buildtools',
                                       'mac' if is_mac else 'linux64', 'gn')
    # Generic GN build.
    path = source_dir.joinpath('out', dirname)
    if not target_cpu:
      target_cpu = 'x64'
    args = (
      'target_os="' + target_os + '" target_cpu="' + target_cpu + '"' +
      ' is_debug=' + ('true' if is_debug else 'false'))
    if is_ios and target_cpu == 'arm64':
      # Disable code signing for iOS device builds since the bots don't have
      # certs installed.
      args += ' ios_enable_code_signing=false'
    elif is_linux:
      # Point at the local copy of clang and a sysroot that were downloaded by
      # gclient runhooks.
      args += ' clang_path="//third_party/linux/clang/linux-amd64"'
      args += ' target_sysroot="//third_party/linux/sysroot"'
    with api.context(cwd=source_dir):
      with sdk(target_os):
        api.step('generate build files',
                 [gn, 'gen', path, '--check', '--args=' + args])
  elif is_win:
    gn = api.path.start_dir.joinpath('buildtools', 'win', 'gn.exe')
    # On Windows, we ought to test:
    # a) x64 OS, x64 handler, x64 client
    # b) x64 OS, x64 handler, x86 client
    # c) x64 OS, x86 handler, x86 client
    # d) x86 OS, x86 handler, x86 client
    #
    # d) used to be tested on _x86 bots, but they have been removed.
    #
    # Two build directories are generated, one for x86 and one for x64, which
    # allows testing a), b), and c). As the bot time is dominated by machine
    # setup and build time, we do not separate these onto separate machines.
    # Additionally, they're all on the same physical machine currently, so
    # there's no upside in parallelism.

    x86_path = source_dir.joinpath('out', dirname + '_x86')
    x64_path = source_dir.joinpath('out', dirname + '_x64')
    args = 'target_os="win" is_debug=' + ('true' if is_debug else 'false')
    with api.context(cwd=source_dir):
      with sdk(target_os, 'x86'):
        api.step('generate build files x86', [
            gn, 'gen', x86_path, '--check',
            '--args=' + args + ' target_cpu="x86"'
        ])
      with sdk(target_os, 'x64'):
        api.step('generate build files x64', [
            gn, 'gen', x64_path, '--check',
            '--args=' + args + ' target_cpu="x64"'
        ])

  def run_tests(build_dir, env=None):
    if is_fuchsia:
      # QEMU instances are very flaky. Disable test running for now. This used
      # to (and should) do `build/run_fuchsia_qemu.py start` before, and `...
      # stop` after calling run_tests.py.
      # https://bugs.chromium.org/p/crashpad/issues/detail?id=219.
      return

    if is_ios and target_cpu == 'arm64':
      # Testing is not supported on iOS devices (arm64).
      return

    # Bringing up the iOS simulator can add a sizable delay per test case.
    timeout_in_minutes = 10 if is_ios else 5

    with api.context(env=env):
      api.step(
          'run tests', [
              'vpython3', '-u',
              source_dir.joinpath('build', 'run_tests.py'), build_dir
          ],
          timeout=timeout_in_minutes * 60)

  ninja = source_dir.joinpath('third_party', 'ninja', 'ninja')
  if is_win:
    with sdk(target_os, 'x86'):
      api.step('compile with ninja x86', [ninja, '-C', x86_path])
    with sdk(target_os, 'x64'):
      api.step('compile with ninja x64', [ninja, '-C', x64_path])
    run_tests(x86_path)
    run_tests(x64_path, env={'CRASHPAD_TEST_32_BIT_OUTPUT': x86_path})
  else:
    with sdk(target_os):
      api.step('compile with ninja', [ninja, '-C', path])
      # On iOS, the tests need to be run before resetting the current SDK,
      # as they use tools from the installed version of Xcode.
      run_tests(path)


def GenTests(api: TEST_DEPS):
  # Only test a single clobber case.
  test = 'crashpad_mac_dbg'
  CRASHPAD_REPO = 'https://chromium.googlesource.com/crashpad/crashpad.git'
  yield api.test(
      test + '_clobber',
      api.properties(target_os='mac', config='Debug', clobber=True) +
      api.buildbucket.ci_build(
          project='crashpad', builder=test, git_repo=CRASHPAD_REPO))

  tests = [
      (test, 'mac', '', None),
      ('crashpad_try_mac_rel', 'mac', '', None),
      ('crashpad_try_win_dbg', 'win', '', None),
      ('crashpad_try_linux_rel', 'linux', '', None),
      ('crashpad_fuchsia_rel', 'fuchsia', '', None),
      ('crashpad_ios_simulator_dbg', 'ios', '', api.path.cache_dir / 'builder'),
      ('crashpad_ios_device_rel', 'ios', 'arm64', None),
  ]
  for t, os, cpu, checkout_dir in tests:
    checkout_dir = checkout_dir or api.path.start_dir
    yield api.test(
        t,
        api.properties(
            config='Debug' if '_dbg' in t else 'Release',
            target_os=os,
            target_cpu=cpu),
        api.buildbucket.ci_build(
            project='crashpad', builder=t, git_repo=CRASHPAD_REPO),
        api.path.exists(
            checkout_dir.joinpath('crashpad', 'build',
                                  'swarming_test_spec.pyl')))
