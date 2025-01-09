# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Buildbot recipe to build and test Dawn standalone using CMake.
"""

DEPS = [
    'depot_tools/bot_update', 'depot_tools/depot_tools', 'depot_tools/gclient',
    'depot_tools/gsutil', 'depot_tools/osx_sdk', 'reclient',
    'recipe_engine/buildbucket', 'recipe_engine/cipd', 'recipe_engine/context',
    'recipe_engine/file', 'recipe_engine/json', 'recipe_engine/path',
    'recipe_engine/platform', 'recipe_engine/properties', 'recipe_engine/step',
    'recipe_engine/time', 'recipe_engine/raw_io'
]

from contextlib import contextmanager
from dataclasses import dataclass
from recipe_engine.recipe_api import Property

PROPERTIES = {
    'target_cpu':
        Property(default=None, kind=str),
    'debug':
        Property(default=False, kind=bool),
    'clang':
        Property(default=False, kind=bool),
    'asan':
        Property(default=False, kind=bool),
    'ubsan':
        Property(default=False, kind=bool),
    'gen_fuzz_corpus':  # TODO(amaiorano): remove once main.star is updated
        Property(default=False, kind=bool),
    'enable_remoteexec':  # TODO(amaiorano): remove once we get rbe working
        Property(default=False, kind=bool),
}

DAWN_REPO = "https://dawn.googlesource.com/dawn"


def _checkout_steps(api):
  '''Checks out Dawn. After this, api.path.checkout_dir returns the dawn root.'''
  # Check out dawn into an un-cached directory in 'cache'. This seems weird,
  # but it allows us to use api.path.cache_dir as a common root for RBE.
  solution_path = api.path.cache_dir / 'uncached'
  api.file.ensure_directory('init cache if not exists', solution_path)

  with api.context(cwd=solution_path):
    # Checkout dawn and its dependencies (specified in DEPS) using gclient.
    api.gclient.set_config('dawn')
    api.gclient.c.got_revision_mapping['dawn'] = 'got_revision'
    # Standalone developer dawn builds want the dawn checkout in the same
    # directory the .gclient file is in.  Bots want it in a directory called
    # 'dawn'.  To make both cases work, the dawn DEPS file pulls deps and runs
    # hooks relative to the variable "root" which is set to . by default and
    # then to 'dawn' on bots here:
    api.gclient.c.solutions[0].custom_vars = {
        'dawn_root': 'dawn',
        'fetch_cmake': 'True',  # Fetch cmake
        'dawn_node': 'True',  # Fetch deps for dawn.node
    }
    if api.reclient.instance:
      api.reclient.use_download_remoteexec_cfg_hook(api.gclient.c.solutions[0])
    update_result = api.bot_update.ensure_checkout()
    api.gclient.runhooks()
  return update_result


# Installs libraries required for Kotlin tests to named cache directory mentioned in luci builder
# configurations. Data in this directory will be persistent and will be
# available between builds
def _install_android_deps(api):
  env_paths = []

  # Install Java 17
  java_ensure_path = api.cipd.EnsureFile()
  java_install_path = api.path.cache_dir / 'java'
  java_ensure_path.add_package("chromium/third_party/jdk",
                               "BXZwbslDFpYhPRuG8hBh2z7ApP36ZG-ZfkBWrkpnPl4C")

  api.cipd.ensure(java_install_path, java_ensure_path, "Install JDK 17")
  env_paths.append(java_install_path / 'bin')

  # Install Android SDK
  android_sdk_ensure_path = api.cipd.EnsureFile()
  android_sdk_install_path = api.path.cache_dir / 'android_sdk'
  android_sdk_packages = {
      'chromium/third_party/android_sdk/public/build-tools/34.0.0': 'latest',
      'chromium/third_party/android_sdk/public/platform-tools': 'latest',
      'chromium/third_party/android_sdk/public/platforms/android-34': 'latest',
      'chromium/third_party/android_sdk/public/cmdline-tools': 'latest'
  }
  for pkg, version in android_sdk_packages.items():
    android_sdk_ensure_path.add_package(pkg, version)

  api.cipd.ensure(android_sdk_install_path, android_sdk_ensure_path,
                  "Install Android SDK")

  # Accept SDK licenses
  android_sdkmanager_path = android_sdk_install_path / 'cmdline-tools' / 'latest' / 'bin' / 'sdkmanager'
  api.step(
      'Accept Android SDK Licenses', [android_sdkmanager_path, '--licenses'],
      stdin=api.raw_io.input_text('y\n' * 100))

  # Install Gradle
  gradle_ensure_path = api.cipd.EnsureFile()
  gradle_install_path = api.path.cache_dir / 'gradle'
  #TODO(b/347893657): Replace flutter/gradle with a custom gradle package for dawn
  gradle_ensure_path.add_package("flutter/gradle", "version:8.9")
  api.cipd.ensure(gradle_install_path, gradle_ensure_path, "Install Gradle 8")
  env_paths.append(gradle_install_path / 'bin')

  return env_paths


def _install_clang(api):
  # 'builder' directory is implicitly cached, so cache clang there
  install_path = api.path.cache_dir / 'builder'

  env_paths = []
  # Install binaries to named cache directory mentioned in luci builder
  # configurations. Data in this directory will be persistent and will be
  # available between builds.
  ensure_file = api.cipd.EnsureFile()
  # CIPD packages and versions for clang.
  # See https://chrome-infra-packages.appspot.com/p/fuchsia/third_party/clang
  # Note: arm64 packages are also available
  package_name = ''
  package_hash = ''
  if api.platform.is_win:
    package_name = 'windows-amd64'
    package_hash = 'yrliUGUdB_0T81Mf6FV-Oz-nF1lgdcEuaJF2MfNhI5EC'
  elif api.platform.is_linux:
    package_name = 'linux-amd64'
    package_hash = '8ii0cvHxegwLJjXM6PekCWB7xzEnFsY7cc2G96VT0VsC'
  # TODO(amaiorano): Right now, we're using api.os_sdk('mac'), but keep this commented
  # out temporarily as we may want to use Clang from CIPD for RBE.
  # elif api.platform.is_mac:
  #   package_name = 'mac-amd64'
  #   package_hash = 'fDkN9wRoOjxDSowpeqX2rRdHF3sgPEbM9ulbizq9kCQC'
  ensure_file.add_package(f'fuchsia/third_party/clang/{package_name}',
                          package_hash, 'clang')
  api.cipd.ensure(install_path, ensure_file, "Install Clang")
  env_paths.append(install_path / 'clang/bin')
  return env_paths


@contextmanager
def windows_sdk(api, source_dir):
  """Sets up environment for MSVC usage"""
  if not api.platform.is_win:
    yield
    return

  try:
    with api.step.nest('Read Windows SDK environment'):
      toolchain_data = api.file.read_json(
          'read build/win_toolchain.json',
          source_dir.joinpath('build', 'win_toolchain.json'),
          test_data={
              'win_sdk':
                  'win_toolchain\\vs_files\\version_hash\\Windows Kits\\10',
              'path':
                  'win_toolchain\\vs_files\\version_hash',
              'runtime_dirs': [
                  'win_toolchain\\vs_files\\version_hash\\sys64',
                  'win_toolchain\\vs_files\\version_hash\\sys32',
                  'win_toolchain\\vs_files\\version_hash\\sysarm64',
              ],
              'wdk':
                  'win_toolchain\\vs_files\\version_hash\\wdk',
          })

      arch_data = api.file.read_json(
          'read SetEnv.x64.json',
          api.path.join(toolchain_data['win_sdk'], 'bin', 'SetEnv.x64.json'),
          test_data={
              'env': {
                  'PATH': [['Windows Kits', '10', 'bin', 'version', 'x64']],
              },
          })

      env_prefixes = {}
      for k in arch_data['env']:
        env_prefixes[k] = [
            api.path.join(*([toolchain_data['path']] + e))
            for e in arch_data['env'][k]
        ]

      env = {
          'WINDOWSSDKDIR': toolchain_data['win_sdk'],
          'WDK_DIR': toolchain_data['wdk']
      }
      env_prefixes['PATH'] += toolchain_data['runtime_dirs'] + [
          toolchain_data['path']
      ]
      # Echo what we've parsed
      log_text = 'env:\n' + '\n'.join([
          f'{k}={v}' for (k, v) in env.items()
      ]) + '\n\nenv_prefixes:\n' + '\n'.join(
          [f'{k}={",".join(v)}' for (k, v) in env_prefixes.items()])
      api.step.empty('Display env and env_prefixes', log_text=log_text)

    with api.context(env=env, env_prefixes=env_prefixes):
      yield
  finally:
    # cl.exe automatically starts background mspdbsrv.exe daemon which
    # needs to be manually stopped so Swarming can tidy up after itself.
    api.step(
        'Kill mspdbsrv (if running)',
        ['taskkill.exe', '/f', '/t', '/im', 'mspdbsrv.exe'],
        raise_on_failure=False,
        ok_ret='any')


def _rbe_host_platform_name(api):
  if api.platform.is_win:
    return 'windows'
  if api.platform.is_linux:
    return 'linux'
  if api.platform.is_mac:
    return 'mac'


def _get_rbe_plaform_from_cfg(api, rewrapper_cfg):

  def parse_line(line):
    line = line.strip()
    if len(line) == 0 or line.startswith('#'):
      return None
    parts = line.split('=', 1)
    parts[0].lstrip('-')
    return (parts[0], True if len(parts) == 1 else parts[1])

  rbe_platform = ''
  cfg_content = api.file.read_text(
      f'read {rewrapper_cfg.name}',
      str(rewrapper_cfg),
      test_data='#comment\nplatform=test',
      include_log=False)
  for line in cfg_content.split('\n'):
    flag = parse_line(line)
    if flag and flag[0] == 'platform':
      rbe_platform = flag[1]
      break
  return rbe_platform


def _rbe_exec_root(api):
  '''Returns base absolute path that contains all required inputs for RBE'''
  # We make sure to checkout Dawn and any required build inputs (e.g. clang)
  # under the cache directory (note: note everything under it is actually
  # cached).
  return api.path.cache_dir


@dataclass
class CMakeFixedArgs:
  '''Args to CMake that shouldn't change for a given build'''
  target_cpu: str
  debug: bool
  clang: bool
  asan: bool
  ubsan: bool
  enable_remoteexec: bool
  build_benchmarks: bool
  build_dxc: bool


# Make a CMake build for Dawn. Returns the build_path, which is unique based
# on cmake args.
def _cmake_build(flavor,
                 api,
                 source_dir,
                 fixed_args: CMakeFixedArgs,
                 dawn_node=False,
                 build_as_other=False,
                 enable_readers_and_writers=True,
                 build_fuzzers=False,
                 targets=None):
  if targets is None:
    targets = ['all']
  with api.step.nest(f'CMake build {flavor}'):
    return _do_cmake_build(flavor, api, source_dir, fixed_args, dawn_node,
                           build_as_other, enable_readers_and_writers,
                           build_fuzzers, targets)


def _do_cmake_build(flavor, api, source_dir, fixed_args: CMakeFixedArgs,
                    dawn_node: bool, build_as_other: bool,
                    enable_readers_and_writers: bool, build_fuzzers: bool,
                    targets: list):
  use_remoteexec = fixed_args.enable_remoteexec and fixed_args.clang and api.reclient.instance
  build_env_vars = {}

  # Cross-compilation with CMake is painful, requiring toolchain files
  # (except with Visual Studio that supprts "-A <platform-name>").
  # For now, don't support cross-compilation and assume the host and target
  # cpus are the same.
  # TODO(amaiorano): Assert that host cpu is same as target cpu
  _ = fixed_args.target_cpu

  def cmake_bool_arg(v: bool):
    return "1" if v else "0"

  cmake_args = [
      '-GNinja',
      f'-DCMAKE_BUILD_TYPE={"DEBUG" if fixed_args.debug else "RELEASE"}',
      f'-DTINT_BUILD_BENCHMARKS={cmake_bool_arg(fixed_args.build_benchmarks)}',
      '-DTINT_RANDOMIZE_HASHES=1',
      '-DDAWN_WERROR=1',
      f'-DDAWN_USE_BUILT_DXC={cmake_bool_arg(fixed_args.build_dxc)}',
      f'-DTINT_BUILD_SPV_READER={cmake_bool_arg(enable_readers_and_writers)}',
      f'-DTINT_BUILD_WGSL_READER={cmake_bool_arg(enable_readers_and_writers)}',
      f'-DTINT_BUILD_GLSL_WRITER={cmake_bool_arg(enable_readers_and_writers)}',
      f'-DTINT_BUILD_HLSL_WRITER={cmake_bool_arg(enable_readers_and_writers)}',
      f'-DTINT_BUILD_MSL_WRITER={cmake_bool_arg(enable_readers_and_writers)}',
      f'-DTINT_BUILD_SPV_WRITER={cmake_bool_arg(enable_readers_and_writers)}',
      f'-DTINT_BUILD_WGSL_WRITER={cmake_bool_arg(enable_readers_and_writers)}',
      f'-DDAWN_ENABLE_ASAN={cmake_bool_arg(fixed_args.asan)}',
      f'-DDAWN_ENABLE_UBSAN={cmake_bool_arg(fixed_args.ubsan)}',
      f'-DDAWN_BUILD_NODE_BINDINGS={cmake_bool_arg(dawn_node)}',
      f'-DTINT_BUILD_AS_OTHER_OS={cmake_bool_arg(build_as_other)}',
  ]
  if fixed_args.clang:
    cmake_args.extend([
        '-DCMAKE_C_COMPILER=clang',
        '-DCMAKE_CXX_COMPILER=clang++',
        f'-DTINT_BUILD_FUZZERS={cmake_bool_arg(build_fuzzers)}',
    ])
    if api.platform.is_linux:
      # On Linux, use the x64 sysroot specified in DEPS
      sysroot = source_dir.joinpath('build/linux/debian_bullseye_amd64-sysroot')
      cmake_args.extend([f'-DCMAKE_SYSROOT={sysroot}'])

  rbe_exec_root = _rbe_exec_root(api)
  if use_remoteexec:
    # Tell CMake to use reclient via it's launcher flags
    # See go/reclient-migration-guide (CMake section)
    rewrapper = source_dir.joinpath('buildtools', 'reclient', 'rewrapper')
    config = source_dir.joinpath(
        'buildtools', 'reclient_cfgs', 'chromium-browser-clang',
        f'rewrapper_{_rbe_host_platform_name(api)}.cfg')
    cmake_args.extend([
        # f'-DCMAKE_C_COMPILER_LAUNCHER={rewrapper};-cfg={config};-exec_root={rbe_exec_root}',
        # f'-DCMAKE_CXX_COMPILER_LAUNCHER={rewrapper};-cfg={config};-exec_root={rbe_exec_root}',
        f'-DCMAKE_C_COMPILER_LAUNCHER={rewrapper};-cfg={config}',
        f'-DCMAKE_CXX_COMPILER_LAUNCHER={rewrapper};-cfg={config}',
    ])

  # Always use the same build directory so that incremental builds are faster.
  # Note that this directory is not cached.
  outdir_name = 'cmake-build'

  build_path = source_dir.joinpath(outdir_name)
  ninja_path = source_dir.joinpath('third_party', 'ninja')
  cmake_path = source_dir.joinpath('tools', 'cmake', 'bin', 'cmake')

  with api.context(cwd=source_dir, env_prefixes={'PATH': [ninja_path]}):
    api.step(
        f'CMake generate step for {flavor}',
        [cmake_path, '-S', str(source_dir), '-B', build_path] + cmake_args)

  step_desc = f'Compile {flavor}'
  if use_remoteexec:
    # RBE build
    ninja_cmd = [
        ninja_path / 'ninja', '-C', build_path, '-j', api.reclient.jobs
    ]
    ninja_cmd.extend(targets)

    # Setup RBE environment required to work with CMake
    build_env_vars['PLATFORM'] = _rbe_host_platform_name(api)
    build_env_vars['RBE_exec_root'] = rbe_exec_root
    build_env_vars['RBE_canonicalize_working_dir'] = 'False'
    rbe_platform = _get_rbe_plaform_from_cfg(api, config)
    if rbe_platform:
      build_env_vars[
          'RBE_platform'] = f'{rbe_platform},InputRootAbsolutePath={rbe_exec_root}'

    build_env_vars['RBE_v'] = '2'

    with api.context(env=build_env_vars):
      # Force remote-only to see if this fails (can't do this via env var)
      with api.reclient.process(
          step_desc, '', source_dir, exec_strategy='remote'):
        api.step(step_desc, ninja_cmd)
  else:
    # Regular cmake build
    def gen_targets():
      for t in targets:
        yield '--target'
        yield t

    cmake_build_cmd = [cmake_path, '--build', build_path, '--parallel'] + list(
        gen_targets())
    with api.context(env=build_env_vars):
      api.step(step_desc, cmake_build_cmd)
  return build_path


def RunSteps(api,
             target_cpu: str,
             debug: bool,
             clang: bool,
             asan: bool,
             ubsan: bool,
             enable_remoteexec: bool = False):
  env = {}
  if api.platform.is_win:
    env['DEPOT_TOOLS_WIN_TOOLCHAIN_ROOT'] = (
        api.path.cache_dir / 'win_toolchain')
  if asan:
    # Disable 'detect_container_overflow' as we're hitting false positives because libc++ is not build with asan.
    # See https://github.com/google/sanitizers/wiki/AddressSanitizerContainerOverflow#false-positives
    env['ASAN_OPTIONS'] = 'detect_container_overflow=0'
  if ubsan:
    env['UBSAN_OPTIONS'] = 'print_stacktrace=1:halt_on_error=1'
  if api.platform.is_linux:
    env['ANDROID_HOME'] = api.path.cache_dir / 'android_sdk'
    env['JAVA_HOME'] = api.path.cache_dir / 'java'

  with api.context(env=env):
    update_result = _checkout_steps(api)
    source_dir = update_result.source_root.path

    env_paths = []
    if clang and not api.platform.is_mac:
      env_paths = _install_clang(api)
    env_paths.append(source_dir.joinpath('tools', 'golang', 'bin'))
    env_paths.append(source_dir.joinpath('third_party', 'depot_tools'))
    if api.platform.is_linux:
      env_paths.extend(_install_android_deps(api))
    with api.context(env_prefixes={'PATH': env_paths}) as _, \
        api.osx_sdk('mac') as _, \
        windows_sdk(api, source_dir) as _:

      # Run shell scripts with bash on Windows
      shell_wrapper = ('bash', '--') if api.platform.is_win else ()

      with api.context(cwd=source_dir):
        # Run these checks only on Linux, no need to check on Win/Mac
        if api.platform.is_linux:
          api.step(
              'Check for no CRLF', ['./tools/check-no-crlf'],
              wrapper=shell_wrapper)
          api.step('Run cpplint', ['./tools/lint'], wrapper=shell_wrapper)
          api.step(
              'Run go tool unittests', ['go', 'test', './...'],
              wrapper=shell_wrapper)
          with api.context(cwd=source_dir.joinpath('tools', 'android')):
            api.step('Run Kotlin unit tests',
                     ['gradle', ':webgpu:testDebugUnitTest'])

      cmake_fixed_args = CMakeFixedArgs(
          target_cpu,
          debug,
          clang,
          asan,
          ubsan,
          enable_remoteexec,
          # Skip benchmarks on Mac to speed up the build
          build_benchmarks=not api.platform.is_mac,
          # Skip dxc on Mac to speed up the build
          build_dxc=not api.platform.is_mac)

      # Only build and test fuzzers on Linux
      build_fuzzers = api.platform.is_linux

      build_path = _cmake_build(
          'default targets',
          api,
          source_dir,
          cmake_fixed_args,
          build_fuzzers=build_fuzzers)
      rel_build_path = str(api.path.relpath(build_path,
                                            source_dir)).replace('\\', '/')

      _cmake_build(
          'default targets with dawn.node enabled',
          api,
          source_dir,
          cmake_fixed_args,
          dawn_node=True,
          build_fuzzers=build_fuzzers,
          targets=['dawn.node'])

      tint_unittests_cmd = [build_path / 'tint_unittests']
      if api.platform.is_win and debug:
        # Exclude DeathTests from Windows Debug builds due to excessive runtimes.
        # See crbug.com/346814503.
        tint_unittests_cmd.append('--gtest_filter=-*DeathTest.*')
      api.step('Run tint_unittests', tint_unittests_cmd)

      if build_fuzzers:
        with api.context(cwd=source_dir):
          api.step(
              'Check fuzzers',
              ['./tools/run', 'fuzz', '--check', '--build', rel_build_path],
              wrapper=shell_wrapper)

      with api.context(cwd=source_dir):
        tint_exe = f'{rel_build_path}/tint{".exe" if api.platform.is_win else ""}'

        api.step('Check generated benchmark inputs header', [
            'python3', './src/tint/cmd/bench/generate_benchmark_inputs.py',
            'wgsl', tint_exe, '--check-stale'
        ])

        if (api.platform.is_win or api.platform.is_mac) and debug:
          # TODO(crbug.com/42251089): GLSL is ~7x slower on Windows debug builds
          # TODO(crbug.com/362783220): IR validation is very slow on Windows (and only enabled in debug)
          e2e_test_formats = 'wgsl,spvasm,msl,hlsl'
        else:
          e2e_test_formats = 'wgsl,spvasm,msl,hlsl,glsl,msl-ir,hlsl-ir'
        api.step(
            'Run Tint end-to-end tests', [
                './tools/run', 'tests', '--tint', tint_exe, '--verbose',
                '--format', e2e_test_formats
            ],
            wrapper=shell_wrapper)

      # Skip building 'other' on Windows as we hit _CRT_SECURE_NO_WARNINGS related to
      # std::getenv, and it's not worth fixing.
      if not api.platform.is_win:
        _cmake_build(
            '_other.cc files',
            api,
            source_dir,
            cmake_fixed_args,
            build_as_other=True,
            build_fuzzers=build_fuzzers,
            targets=['tint_cmd_tint_cmd'])

      _cmake_build(
          'disabled readers and writers',
          api,
          source_dir,
          cmake_fixed_args,
          enable_readers_and_writers=False,
          build_fuzzers=False,  # Cannot build fuzzers without readers/writers enabled
          targets=['tint_cmd_tint_cmd'])


def GenTests(api):
  yield api.test(
      'linux',
      api.reclient.properties(),
      api.platform('linux', 64),
      api.buildbucket.ci_build(
          project='dawn', builder='linux', git_repo=DAWN_REPO),
  )
  yield api.test(
      'linux_remoteexec',
      api.reclient.properties(),
      api.properties(clang=True, enable_remoteexec=True),
      api.platform('linux', 64),
      api.buildbucket.ci_build(
          project='dawn', builder='linux', git_repo=DAWN_REPO),
  )
  yield api.test(
      'linux_asan',
      api.reclient.properties(),
      api.properties(asan=True),
      api.platform('linux', 64),
      api.buildbucket.ci_build(
          project='dawn', builder='linux', git_repo=DAWN_REPO),
  )
  yield api.test(
      'linux_ubsan',
      api.reclient.properties(),
      api.properties(ubsan=True),
      api.platform('linux', 64),
      api.buildbucket.ci_build(
          project='dawn', builder='linux', git_repo=DAWN_REPO),
  )
  yield api.test(
      'mac',
      api.reclient.properties(),
      api.platform('mac', 64),
      api.buildbucket.ci_build(
          project='dawn', builder='mac', git_repo=DAWN_REPO),
  )
  yield api.test(
      'mac_debug',
      api.reclient.properties(),
      api.platform('mac', 64),
      api.properties(debug=True),
      api.buildbucket.ci_build(
          project='dawn', builder='mac', git_repo=DAWN_REPO),
  )
  yield api.test(
      'mac_remoteexec',
      api.reclient.properties(),
      api.properties(clang=True, enable_remoteexec=True),
      api.platform('mac', 64),
      api.buildbucket.ci_build(
          project='dawn', builder='mac', git_repo=DAWN_REPO),
  )
  yield api.test(
      'win',
      api.reclient.properties(),
      api.platform('win', 64),
      api.buildbucket.ci_build(
          project='dawn', builder='win', git_repo=DAWN_REPO),
  )
  yield api.test(
      'win_debug',
      api.reclient.properties(),
      api.platform('win', 64),
      api.properties(debug=True),
      api.buildbucket.ci_build(
          project='dawn', builder='win', git_repo=DAWN_REPO),
  )
  yield api.test(
      'win_clang',
      api.reclient.properties(),
      api.platform('win', 64),
      api.properties(clang=True),
      api.buildbucket.ci_build(
          project='dawn', builder='win', git_repo=DAWN_REPO),
  )
  yield api.test(
      'win_remoteexec',
      api.reclient.properties(),
      api.platform('win', 64),
      api.properties(clang=True, enable_remoteexec=True),
      api.buildbucket.ci_build(
          project='dawn', builder='win', git_repo=DAWN_REPO),
  )
