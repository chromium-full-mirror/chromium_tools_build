# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from contextlib import contextmanager
from recipe_engine.recipe_api import Property

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, presentation_utils
from RECIPE_MODULES.depot_tools import bot_update, gclient, osx_sdk
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cas,
    context,
    defer,
    file,
    path,
    platform,
    properties,
    raw_io,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  cas: cas.API
  chromium: chromium.API
  context: context.API
  defer: defer.API
  file: file.API
  gclient: gclient.API
  osx_sdk: osx_sdk.API
  path: path.API
  platform: platform.API
  presentation_utils: presentation_utils.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  file: file.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  step: step.TEST_API

# This recipe historically parsed the builder name for test behavior. The
# recipe framework prefers properties, which has the added benefit of requiring
# less coordination between repositories to add new configurations. For now,
# some properties are redundant with calls to _Config.has_token() below. That
# logic will be removed as the builder definitions pass in the same values.
PROPERTIES = {
    'android':
        Property(default=False, kind=bool, help='whether to build for Android'),
    'check_pregenerated_files':
        Property(
            default=True, kind=bool,
            help='whether to check pregenerated files'),
    'check_stack':
        Property(
            default=False,
            kind=bool,
            help='whether to run the check_stack script'),
    'clang':
        Property(
            default=None,
            kind=bool,
            help='if true, uses the vendored copy of clang in util/bot'),
    'cmake_args':
        Property(
            default={},
            kind=dict,
            help='a dictionary of variables to configure CMake with'),
    'gclient_vars':
        Property(
            default={},
            kind=dict,
            help='a dictionary of variables to pass into gclient'),
    'msvc_target':
        Property(
            default=None,
            kind=str,
            help='the target architecture to configure MSVC with'),
    'prefixed_symbols':
        Property(
            default=False,
            kind=bool,
            help='whether to build with a BORINGSSL_PREFIX'),
    'check_prefixed_symbols':
        Property(
            default=False,
            kind=bool,
            help='whether to check if BORINGSSL_PREFIX was actually applied; takes no effect without prefixed_symbols; will in the future be removed and prefixed_symbols will control checking too'
        ),
    'runner_args':
        Property(
            default=[],
            kind=list,
            help='parameters to pass to runner',
        ),
    'run_unit_tests':
        Property(default=True, kind=bool, help='whether to run unit tests'),
    'run_ssl_tests':
        Property(
            default=True, kind=bool, help='whether to run SSL protocol tests'),
    'rust':
        Property(
            default=False,
            kind=bool,
            help='whether to build the Rust crates, '
            'and test them (if run_unit_tests is also True)'),
    'sde':
        Property(default=False, kind=bool, help='whether to run tests on SDE'),
    'upload_to_cas':
        Property(
            default=[],
            kind=list,
            help='which files in the build directory to upload to CAS'),
}

# The value of BORINGSSL_PREFIX to use if the prefixed_symbols property is set.
BORINGSSL_PREFIX = "the_boringssl_prefix"


def _GetHostToolSuffix(platform):
  if platform.is_linux:
    if platform.bits == 64:
      return 'linux64'
  elif platform.is_mac:
    return 'mac'
  elif platform.is_win:
    return 'win32'
  raise ValueError('unknown platform')  # pragma: no cover


def _GetHostExeSuffix(platform):
  if platform.is_win:
    return '.exe'
  return ''


def _WindowsCMakeWorkaround(path):
  # CMake does not allow backslashes in several path variables. It writes the
  # string out to a cmake file with configure_file() and fails to escape it
  # correctly. See https://gitlab.kitware.com/cmake/cmake/issues/16254.
  return str(path).replace('\\', '/')


def _GetHostCMakeArgs(platform, bot_utils):
  args = {}
  if platform.is_win:
    args['CMAKE_ASM_NASM_COMPILER'] = _WindowsCMakeWorkaround(bot_utils /
                                                              'nasm-win32.exe')
  return args


def _GetClangPath(platform, bot_utils, cxx):
  if platform.is_win:
    return bot_utils.joinpath('llvm-build', 'bin', 'clang-cl.exe')
  return bot_utils.joinpath('llvm-build', 'bin',
                            ('clang++' if cxx else 'clang'))


class _Config:

  def __init__(self, android, buildername, clang, cmake_args, gclient_vars,
               msvc_target, prefixed_symbols, runner_args, run_ssl_tests,
               run_unit_tests, rust, sde):
    self.android = android
    self.buildername = buildername
    self.clang = clang
    self.cmake_args = cmake_args
    self.gclient_vars = gclient_vars
    self.msvc_target = msvc_target
    self.prefixed_symbols = prefixed_symbols
    self.runner_args = runner_args
    self.run_ssl_tests = run_ssl_tests
    self.run_unit_tests = run_unit_tests
    self.rust = rust
    self.sde = sde

  def has_token(self, token):
    # Builder names are a sequence of tokens separated by underscores.
    #
    # TODO(davidben): Migrate uses of this to properties.
    return '_' + token + '_' in '_' + self.buildername + '_'

  def get_gclient_vars(self, platform):
    ret = {}
    if self.rust:
      ret['checkout_rust'] = True
    if self.sde:
      ret['checkout_sde'] = True
    if platform.is_win:
      ret['checkout_nasm'] = True
    ret.update(self.gclient_vars)
    return ret

  def get_target_cmake_args(self, src, ninja_path, platform):
    bot_utils = src.joinpath('util', 'bot')
    args = {'CMAKE_MAKE_PROGRAM': ninja_path}
    if self.clang:
      adjust_path = _WindowsCMakeWorkaround if platform.is_win else lambda x: x
      args['CMAKE_C_COMPILER'] = adjust_path(
          _GetClangPath(platform, bot_utils, cxx=False))
      args['CMAKE_CXX_COMPILER'] = adjust_path(
          _GetClangPath(platform, bot_utils, cxx=True))
    if self.android:
      args['CMAKE_TOOLCHAIN_FILE'] = bot_utils.joinpath(
          'android_ndk', 'build', 'cmake', 'android.toolchain.cmake')
    if self.prefixed_symbols:
      args['BORINGSSL_PREFIX'] = BORINGSSL_PREFIX
    args.update(self.cmake_args)
    return args

  def get_target_msvc_prefix(self, bot_utils):
    if self.msvc_target is not None:
      return ['python3', bot_utils / 'vs_env.py', self.msvc_target]
    return []

  def get_target_env(self, bot_utils, platform):
    env = {}
    if self.clang:
      # TODO(davidben): detect_stack_use_after_return became default in
      # https://reviews.llvm.org/D124057. Can we remove it?
      env['ASAN_OPTIONS'] = 'detect_stack_use_after_return=1'
      env['ASAN_SYMBOLIZER_PATH'] = bot_utils.joinpath(
          'llvm-build', 'bin', 'llvm-symbolizer' + _GetHostExeSuffix(platform))
      env['MSAN_SYMBOLIZER_PATH'] = bot_utils.joinpath(
          'llvm-build', 'bin', 'llvm-symbolizer' + _GetHostExeSuffix(platform))
    return env


@contextmanager
def _CleanupMSVC(api):
  try:
    yield
  finally:
    if api.platform.is_win:
      # cl.exe automatically starts background mspdbsrv.exe daemon which needs
      # to be manually stopped so Swarming can tidy up after itself.
      api.step(
          'taskkill mspdbsrv',
          ['taskkill.exe', '/f', '/t', '/im', 'mspdbsrv.exe'],
          ok_ret='any')


def RunSteps(api: DEPS, android, check_prefixed_symbols,
             check_pregenerated_files, check_stack, clang, cmake_args,
             gclient_vars, msvc_target, prefixed_symbols, runner_args,
             run_ssl_tests, run_unit_tests, rust, sde, upload_to_cas):
  # Use keyword arguments to avoid accidentally mixing them.
  config = _Config(
      android=android,
      buildername=api.buildbucket.builder_name,
      clang=clang,
      cmake_args=cmake_args,
      gclient_vars=gclient_vars,
      msvc_target=msvc_target,
      prefixed_symbols=prefixed_symbols,
      runner_args=runner_args,
      run_ssl_tests=run_ssl_tests,
      run_unit_tests=run_unit_tests,
      rust=rust,
      sde=sde)

  # Validate the Rust configuration.
  if config.rust:
    rust_bindings_found = 'RUST_BINDINGS' in config.cmake_args
    api.step.empty(
        'check for RUST_BINDINGS',
        status=(api.step.SUCCESS if rust_bindings_found else api.step.FAILURE),
        step_text=('RUST_BINDINGS %s found in cmake_args' %
                   ('was' if rust_bindings_found else 'was not')))

  # Print the kernel version on Linux builders. BoringSSL is sensitive to
  # whether the kernel has getrandom support.
  if api.platform.is_linux:
    api.step('uname', ['uname', '-a'])

  # Sync and pull in everything.
  api.gclient.set_config('boringssl')
  if config.android:
    api.gclient.c.target_os.add('android')
  api.gclient.c.solutions[0].custom_vars = config.get_gclient_vars(api.platform)
  cache_dir = api.path.cache_dir / 'builder'
  with api.context(cwd=cache_dir):
    api.bot_update.ensure_checkout()
    api.gclient.runhooks()

  # Set up paths.
  src = cache_dir / 'boringssl'
  bot_utils = src.joinpath('util', 'bot')
  goroot = bot_utils.joinpath('golang')
  adb_path = bot_utils.joinpath('android_sdk', 'public', 'platform-tools',
                                'adb')
  sde_path = bot_utils.joinpath('sde-' + _GetHostToolSuffix(api.platform),
                                'sde' + _GetHostExeSuffix(api.platform))
  build_dir = src.joinpath('build')
  runner_dir = src.joinpath('ssl', 'test', 'runner')
  ninja_path = bot_utils.joinpath('ninja', 'ninja')
  rust_dir = src.joinpath('rust')

  env = {}
  env_prefixes = {}
  # Point to the packaged copy of Go.
  env['GOROOT'] = goroot
  env_prefixes['PATH'] = [goroot / 'bin']
  # Point Go's module and build caches to reused cache directories.
  env['GOCACHE'] = api.path.cache_dir / 'gocache'
  env['GOPATH'] = api.path.cache_dir / 'gopath'
  # Disable modifications to go.mod so missing entries are treated as an error
  # instead.
  env['GOFLAGS'] = '-mod=readonly'
  # Set up the environment for the Rust toolchain.
  clang_path = _GetClangPath(api.platform, bot_utils, cxx=False)
  if config.rust:
    # Point to packaged copy of Rust toolchain, containing bindgen and cargo.
    env_prefixes['PATH'].append(bot_utils / 'rust-toolchain' / 'bin')
    # Point to the build directory where bindgen output is expected.
    env['BORINGSSL_BUILD_DIR'] = build_dir
    # Ask clang for its resource directory, and pass it to bindgen so it can
    # find the right system header files.
    resource_dir = api.step(
        'get clang resource dir', [clang_path, '-print-resource-dir'],
        stdout=api.raw_io.output_text()).stdout.strip()
    env['BINDGEN_EXTRA_CLANG_ARGS'] = '-resource-dir=' + resource_dir
    if config.prefixed_symbols:
      env['BORINGSSL_PREFIX'] = BORINGSSL_PREFIX
  if api.platform.is_win:
    # On Windows, set %PATH% to include the build_dir, which is where DLLs will
    # be placed. Without this, in a shared library build, bssl_shim won't find
    # libcrypto's DLL.
    env_prefixes['PATH'].append(build_dir)

  # If building with MSVC, all commands must run with an environment wrapper.
  # This is necessary both to find the toolchain and the runtime dlls. Rather
  # than copy the runtime to every directory where a binary is installed, just
  # run the tests with the toolchain prefix as well.
  msvc_prefix = config.get_target_msvc_prefix(bot_utils)

  with api.context(
      env=env,
      env_prefixes=env_prefixes), api.osx_sdk('ios'), _CleanupMSVC(api):
    # Check pregenerated files.
    if check_pregenerated_files and api.path.exists(
        src.joinpath('util', 'pregenerate')):
      cmd = ['go', 'run', './util/pregenerate', '-check']
      if not api.platform.is_mac:
        cmd += ['-clang', clang_path]
      if api.platform.is_win:
        cmd += [
            '-perl',
            bot_utils.joinpath('perl-win32', 'perl', 'bin', 'perl.exe')
        ]
      with api.context(cwd=src):
        api.step('check pregenerated files', msvc_prefix + cmd)

    # CMake is stateful, so do a clean build. BoringSSL builds quickly enough
    # that this isn't a concern.
    api.file.rmtree('clean', build_dir)
    api.file.ensure_directory('mkdir', build_dir)

    # Build BoringSSL itself.
    cmake_dir = bot_utils.joinpath('cmake')
    cmake = cmake_dir.joinpath('bin', 'cmake' + _GetHostExeSuffix(api.platform))
    cmake_args = _GetHostCMakeArgs(api.platform, bot_utils)
    cmake_args.update(
        config.get_target_cmake_args(src, ninja_path, api.platform))
    with api.context(cwd=build_dir):
      api.step(
          'cmake', msvc_prefix + [cmake, '-GNinja'] +
          ['-D%s=%s' % (k, v) for (k, v) in sorted(cmake_args.items())] + [src])

    try:
      api.step('ninja', msvc_prefix + [ninja_path, '-C', build_dir, '-v'])

      # Build the Rust crates.
      cargo = 'cargo' + _GetHostExeSuffix(api.platform)
      if config.rust:
        with api.context(cwd=rust_dir):
          api.step(
              'cargo build',
              msvc_prefix + [cargo, 'build', '--all-targets', '--keep-going'])
          api.step(
              'cargo build (all features)', msvc_prefix + [
                  cargo, 'build', '--all-targets', '--all-features',
                  '--keep-going'
              ])

      with api.defer.context() as defer:
        if check_stack:
          defer(api.step, 'check stack', [
              'go', 'run',
              src.joinpath('util', 'check_stack.go'),
              build_dir.joinpath('bssl')
          ])

        with api.context(cwd=src):
          defer(api.step, 'check filenames',
                ['go', 'run',
                 src.joinpath('util', 'check_filenames.go')])

        if prefixed_symbols and check_prefixed_symbols:
          with api.context(cwd=build_dir):
            defer(api.step, 'check prefixed symbols', [
                'go', 'run',
                src.joinpath('util', 'audit_symbols.go'),
                '-ignore-symbols-with', BORINGSSL_PREFIX
            ])

        with api.context(cwd=src):
          # Determine the list of Go tests to run.
          go_tests_str = api.file.read_text(
              'read go tests', src.joinpath('util', 'go_tests.txt'))
          go_tests = [t for t in go_tests_str.split('\n') if t]
          defer(api.step, 'go tests', ['go', 'test', '-v'] + go_tests)

        env = config.get_target_env(bot_utils, api.platform)

        # Run the unit tests.
        if config.run_unit_tests:
          with api.context(cwd=src, env=env):
            all_tests_args = []
            if config.sde:
              all_tests_args += ['-sde', '-sde-path', sde_path]
            if config.android:
              defer(api.step, 'unit tests', [
                  'go',
                  'run',
                  api.path.join('util', 'run_android_tests.go'),
                  '-build-dir',
                  build_dir,
                  '-adb',
                  adb_path,
                  '-suite',
                  'unit',
                  '-all-tests-args',
                  ' '.join(all_tests_args),
              ])
            else:
              defer(
                  api.step, 'unit tests', msvc_prefix + [
                      'go',
                      'run',
                      api.path.join('util', 'all_tests.go'),
                  ] + all_tests_args)

        # Run the SSL tests.
        if config.run_ssl_tests:
          runner_args = ['-pipe']
          if config.has_token('fuzz'):
            runner_args += ['-fuzzer', '-shim-config', 'fuzzer_mode.json']
          # Limit the number of workers on Android and Mac, to avoid flakiness.
          # https://crbug.com/boringssl/192
          # https://crbug.com/boringssl/199
          if api.platform.is_mac or config.android:
            runner_args += ['-num-workers', '1']
          runner_args += config.runner_args
          if config.android:
            with api.context(cwd=src, env=env):
              defer(api.step, 'ssl tests', [
                  'go',
                  'run',
                  api.path.join('util', 'run_android_tests.go'),
                  '-build-dir',
                  build_dir,
                  '-adb',
                  adb_path,
                  '-suite',
                  'ssl',
                  '-runner-args',
                  ' '.join(runner_args),
              ])
          else:
            with api.context(cwd=runner_dir, env=env):
              defer(api.step, 'ssl tests',
                    msvc_prefix + ['go', 'test'] + runner_args)

        # Run the Rust tests.
        if config.rust and config.run_unit_tests:
          with api.context(cwd=rust_dir):
            defer(
                api.step, 'rust tests', msvc_prefix +
                [cargo, 'test', '--all-targets', '--no-fail-fast'])
            defer(
                api.step, 'rust tests (all features)', msvc_prefix + [
                    cargo, 'test', '--all-targets', '--all-features',
                    '--no-fail-fast'
                ])
    finally:
      if upload_to_cas:
        output_dir = api.path.mkdtemp('debug_artifacts')
        with api.step.nest('Collect debug artifacts'):
          parent_dirs = set()
          for pattern in upload_to_cas:
            paths = api.file.glob_paths("Locate %s" % pattern, build_dir,
                                        pattern)
            for src_path in paths:
              rel_path = src_path.relative_to(build_dir)
              dest_path = output_dir / rel_path
              parent_dir = api.path.dirname(dest_path)
              if parent_dir != output_dir and parent_dir not in parent_dirs:
                parent_rel_path = parent_dir.relative_to(output_dir)
                api.file.ensure_directory('mkdir %s' % parent_rel_path,
                                          parent_dir)
                parent_dirs.add(parent_dir)
              api.file.copy("Export %s" % rel_path, src_path, dest_path)
        api.cas.archive("Upload debug artifacts to CAS", output_dir)


def _CIBuild(api, builder):
  return api.buildbucket.ci_build(
      project='boringssl',
      builder=builder,
      git_repo='https://boringssl.googlesource.com/boringssl')


def _TryBuild(api, builder):
  return api.buildbucket.try_build(
      project='boringssl',
      builder=builder,
      git_repo='https://boringssl.googlesource.com/boringssl')


def GenTests(api: TEST_DEPS):
  mock_go_tests = api.step_data(
      'read go tests',
      api.file.read_text("./util/ar\n./util/fipstools/delocate\n"))

  mock_clang_resource_dir = api.step_data(
      'get clang resource dir',
      # This would be an absolute path in production.
      stdout=api.raw_io.output_text(
          'boringssl/util/bot/llvm-build/lib/clang/99\n'))

  mock_debug_artifacts = api.step_data(
      'Collect debug artifacts.Locate **/*.a',
      api.file.glob_paths([
          'libcrypto.a',
          'ssl/libssl.a',
      ]),
  )

  tests = [
      ('linux', api.platform('linux', 64), {}),
      ('mac', api.platform('mac', 64), {}),
      ('win64', api.platform('win', 64), {
          'msvc_target': 'x64'
      }),
      ('android_aarch64', api.platform('linux', 64), {
          "android": True,
          "cmake_args": {
              "ANDROID_ABI": "arm64-v8a",
              "ANDROID_PLATFORM": "android-21",
          },
      }),
      ('linux_fuzz', api.platform('linux', 64), {
          "clang": True,
          "cmake_args": {
              "FUZZ": "1",
              "LIBFUZZER_FROM_DEPS": "1",
          },
          "gclient_vars": {
              "checkout_fuzzer": True,
          },
      }),
      ('linux_shared', api.platform('linux', 64), {
          "cmake_args": {
              "BUILD_SHARED_LIBS": "1",
          },
      }),
      ('linux_prefixed', api.platform('linux', 64), {
          "prefixed_symbols": True,
          "check_prefixed_symbols": True,
      }),
  ]
  for (buildername, host_platform, props) in tests:
    yield api.test(
        buildername,
        host_platform,
        _CIBuild(api, buildername),
        mock_go_tests,
        api.properties(**props),
    )

  yield api.test(
      'check_pregenerated_files',
      api.platform('linux', 64),
      _CIBuild(api, 'linux'),
      api.path.exists(
          api.path.cache_dir.joinpath('builder', 'boringssl', 'util',
                                      'pregenerate')),
      mock_go_tests,
  )

  yield api.test(
      'check_pregenerated_files_mac',
      api.platform('mac', 64),
      _CIBuild(api, 'mac'),
      api.path.exists(
          api.path.cache_dir.joinpath('builder', 'boringssl', 'util',
                                      'pregenerate')),
      mock_go_tests,
  )

  yield api.test(
      'check_pregenerated_files_win',
      api.platform('win', 64),
      _CIBuild(api, 'win64'),
      api.properties(msvc_target='x64'),
      api.path.exists(
          api.path.cache_dir.joinpath('builder', 'boringssl', 'util',
                                      'pregenerate')),
      mock_go_tests,
  )

  yield api.test(
      'check_pregenerated_files_failed',
      api.platform('linux', 64),
      _CIBuild(api, 'linux'),
      api.path.exists(
          api.path.cache_dir.joinpath('builder', 'boringssl', 'util',
                                      'pregenerate')),
      api.override_step_data('check pregenerated files', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'check_pregenerated_files_failed_mac',
      api.platform('mac', 64),
      _CIBuild(api, 'mac'),
      api.path.exists(
          api.path.cache_dir.joinpath('builder', 'boringssl', 'util',
                                      'pregenerate')),
      api.override_step_data('check pregenerated files', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'check_pregenerated_files_failed_win',
      api.platform('win', 64),
      _CIBuild(api, 'win64'),
      api.properties(msvc_target='x64'),
      api.path.exists(
          api.path.cache_dir.joinpath('builder', 'boringssl', 'util',
                                      'pregenerate')),
      api.override_step_data('check pregenerated files', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'linux_sde',
      api.platform('linux', 64),
      _CIBuild(api, 'linux_sde'),
      mock_go_tests,
      api.properties(
          cmake_args={"CMAKE_BUILD_TYPE": "RelWithAsserts"},
          run_ssl_tests=False,
          sde=True),
  )

  rust_tests = [
      ('linux', api.platform('linux', 64), "x86_64-unknown-linux-gnu", {}),
      ('linux_prefixed', api.platform('linux',
                                      64), "x86_64-unknown-linux-gnu", {
                                          "prefixed_symbols": True
                                      }),
      ('win64', api.platform('win', 64), "x86_64-pc-windows-msvc", {
          "msvc_target": "x64"
      }),
  ]
  for (builder_prefix, host_platform, rust_bindings_target_triple,
       addl_props) in rust_tests:
    yield api.test(
        builder_name := builder_prefix + '_rust',
        host_platform,
        _CIBuild(api, builder_name),
        api.properties(
            rust=True,
            cmake_args={
                "RUST_BINDINGS": rust_bindings_target_triple,
            },
            upload_to_cas=['**/*.a'],
            **addl_props),
        mock_clang_resource_dir,
        mock_go_tests,
        mock_debug_artifacts,
    )

    yield api.test(
        builder_name := builder_prefix + '_rust_compile_only',
        host_platform,
        _CIBuild(api, builder_name),
        api.properties(
            rust=True,
            cmake_args={
                "RUST_BINDINGS": rust_bindings_target_triple,
            },
            run_unit_tests=False,
            **addl_props),
        mock_clang_resource_dir,
        mock_go_tests,
    )

    yield api.test(
        builder_name := builder_prefix + '_failed_rust_bindings_missing',
        host_platform,
        _CIBuild(api, builder_name),
        api.properties(rust=True, **addl_props),
        api.expect_status('FAILURE'),
    )

  yield api.test(
      'failed_filenames',
      api.platform('linux', 64),
      _CIBuild(api, 'linux'),
      mock_go_tests,
      api.override_step_data('check filenames', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'failed_go_tests',
      api.platform('linux', 64),
      _CIBuild(api, 'linux'),
      mock_go_tests,
      api.override_step_data('go tests', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'failed_unit_tests',
      api.platform('linux', 64),
      _CIBuild(api, 'linux'),
      mock_go_tests,
      api.override_step_data('unit tests', retcode=1),
      api.expect_status('FAILURE'),
  )

  # Test that the cleanup step works correctly with test failures.
  yield api.test(
      'failed_unit_tests_win',
      api.platform('win', 64),
      _CIBuild(api, 'win64'),
      mock_go_tests,
      api.properties(msvc_target='x64'),
      api.override_step_data('unit tests', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'failed_ssl_tests',
      api.platform('linux', 64),
      _CIBuild(api, 'linux'),
      mock_go_tests,
      api.override_step_data('ssl tests', retcode=1),
      api.expect_status('FAILURE'),
  )

  # The taskkill step may fail if mspdbsrv has already exitted. This should
  # still be accepted.
  yield api.test(
      'failed_taskkill',
      api.platform('win', 64),
      _CIBuild(api, 'win64'),
      mock_go_tests,
      api.properties(msvc_target='x64'),
      api.override_step_data('taskkill mspdbsrv', retcode=1),
  )

  yield api.test(
      'gerrit_cl',
      api.platform('linux', 64),
      _TryBuild(api, 'linux'),
      mock_go_tests,
  )

  yield api.test(
      'check_stack',
      api.platform('linux', 64),
      _CIBuild(api, 'linux'),
      mock_go_tests,
      api.properties(check_stack=True),
  )

  yield api.test(
      'check_stack_failed',
      api.platform('linux', 64),
      _CIBuild(api, 'linux'),
      mock_go_tests,
      api.properties(check_stack=True),
      api.override_step_data('check stack', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'skip_unit_tests',
      api.platform('linux', 64),
      _CIBuild(api, 'buildername'),
      mock_go_tests,
      api.properties(run_unit_tests=False),
  )
  yield api.test(
      'skip_ssl_tests',
      api.platform('linux', 64),
      _CIBuild(api, 'buildername'),
      mock_go_tests,
      api.properties(run_ssl_tests=False),
  )
  yield api.test(
      'skip_both',
      api.platform('linux', 64),
      _CIBuild(api, 'buildername'),
      mock_go_tests,
      api.properties(run_unit_tests=False, run_ssl_tests=False),
  )

  yield api.test(
      'win_arm64_compile',
      api.platform('win', 64),
      _CIBuild(api, 'buildername'),
      mock_go_tests,
      api.properties(
          clang=True,
          cmake_args={
              'CMAKE_SYSTEM_NAME': 'Windows',
              'CMAKE_SYSTEM_PROCESSOR': 'arm64',
              'CMAKE_ASM_FLAGS': '-target arm64-windows',
              'CMAKE_CXX_FLAGS': '-target arm64-windows',
              'CMAKE_C_FLAGS': '-target arm64-windows',
          },
          gclient_vars={
              'checkout_nasm': False,
          },
          msvc_target='arm64',
          run_unit_tests=False,
          run_ssl_tests=False,
      ),
  )

  yield api.test(
      'linux_fuzz_properties',
      api.platform('linux', 64),
      _CIBuild(api, 'buildername'),
      mock_go_tests,
      api.properties(
          clang=True,
          cmake_args={
              'FUZZ': '1',
              'LIBFUZZER_FROM_DEPS': '1',
          },
          gclient_vars={
              'checkout_fuzzer': True,
          },
          runner_args=['-fuzzer', '-shim-config', 'fuzzer_mode.json'],
      ),
  )
