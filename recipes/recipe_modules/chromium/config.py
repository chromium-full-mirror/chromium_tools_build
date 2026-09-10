# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import pipes

from recipe_engine.config import config_item_context, ConfigGroup
from recipe_engine.config import Dict, List, Single, Static, Set, BadConf

# Because of the way that we use decorators, pylint can't figure out the proper
# type signature of functions annotated with the @config_ctx decorator.
# pylint: disable=E1123

HOST_PLATFORMS = ('linux', 'win', 'mac')
TARGET_PLATFORMS = HOST_PLATFORMS + ('ios', 'android', 'chromeos', 'fuchsia')
HOST_TARGET_BITS = (32, 64)
HOST_ARCHS = ('intel', 'arm')
TARGET_ARCHS = HOST_ARCHS + ('mips', 'mipsel', 'riscv64')
BUILD_CONFIGS = ('Release', 'Debug')
PROJECT_GENERATORS = ('gn', 'mb')


def check(val, potentials):
  assert val in potentials, (val, potentials)
  return val


# Schema for config items in this module.
# Note: The default values for these parameters are defined via recipe_magic in
# chromium/api.py:get_config_defaults().
def BaseConfig(
  HOST_PLATFORM,
  DEFAULT_HOST_PLATFORM,
  HOST_ARCH,
  HOST_BITS,
  TARGET_PLATFORM,
  TARGET_ARCH,
  TARGET_BITS,
  BUILD_CONFIG,
  TARGET_CROS_BOARDS,
  CROS_BOARDS_WITH_QEMU_IMAGES,
  TEST_ONLY,
):
  equal_fn = lambda tup: '%s=%s' % (tup[0], pipes.quote(str(tup[1])))
  return ConfigGroup(
    compile_py=ConfigGroup(
      default_targets=Set(str),
      build_args=List(str),
      compiler=Single(str, required=False),
      mode=Single(str, required=False),
      prune_venv=Single(bool, empty_val=False, required=False),
    ),
    gyp_env=ConfigGroup(
      # VR version is deprecated, use XR version going forward.
      DOWNLOAD_XR_TEST_APKS=Single(int, required=False),
      GYP_DEFINES=Dict(equal_fn, ' '.join, (str, int)),
    ),
    # This allows clients to opt out of using GYP variables in the
    # environment.
    # TODO(machenbach): This does not expand to Chromium's runtests yet.
    use_gyp_env=Single(bool, empty_val=True, required=False),
    env=ConfigGroup(
      # Additional directories to add to PATH when running chromium module
      # steps, as paths relative to the top-level repo
      PATH=List(str),
      FORCE_MAC_TOOLCHAIN=Single(int, required=False),
      FORCE_MAC_SDK_MIN=Single(str, required=False),
    ),
    mac_toolchain=ConfigGroup(
      enabled=Single(bool, empty_val=False, required=False),
      # Xcode installer configs. These normally don't change with Xcode
      # version.
      installer_cipd_package=Single(str),
      installer_version=Single(str),
      installer_cmd=Single(str),
      kind=Single(str),
    ),
    project_generator=ConfigGroup(
      tool=Single(str, empty_val='mb'),
      # Path to the config file for the project generator, relative to the
      # top-level repo
      config_path=Single(str),
      args=Set(str),
      # Path to isolate map files to use when invoking the project
      # generator, relative to the top-level repo
      isolate_map_paths=List(str),
      # TODO(crbug.com/1060857): Remove this once swarming task templates
      # support command prefixes.
      use_luci_auth=Single(bool, empty_val=False, required=False),
      # Allow duplicated entries from isolate map files.
      allow_dup_isolate_entry=Single(bool, empty_val=False, required=False),
    ),
    cros_sdk=ConfigGroup(
      external=Single(bool, empty_val=True, required=False),
      args=List(str),
    ),
    runtests=ConfigGroup(
      enable_asan=Single(bool, empty_val=False, required=False),
      enable_lsan=Single(bool, empty_val=False, required=False),
      enable_msan=Single(bool, empty_val=False, required=False),
      enable_tsan=Single(bool, empty_val=False, required=False),
      run_asan_test=Single(bool, required=False),
    ),
    # Path to the directory where targets spec files will be read for
    # the builder, relative to the top-level repo
    targets_spec_dir=Single(str),
    # Path to the analyze config file used for configuring ignores/exclusions
    # for analyze (e.g. //testing/buildbot/trybot_analyze_config.json),
    # relative to the top-level repo. If not provided, then
    # trybot_analyze_config.json in targets_spec_dir will be used.
    analyze_config_path=Single(str, required=False),
    use_tot_clang=Single(bool, empty_val=False, required=False),
    # Indicates that the build-dir is shared across multiple builders. May use
    # a builder-specific build-dir name otherwise.
    shared_build_dir=Single(bool, empty_val=False, required=False),
    # Some platforms do not have a 1:1 correlation of BUILD_CONFIG to what is
    # passed as --target on the command line.
    build_config_fs=Single(str),
    BUILD_CONFIG=Static(check(BUILD_CONFIG, BUILD_CONFIGS)),
    HOST_PLATFORM=Static(
      check(HOST_PLATFORM or DEFAULT_HOST_PLATFORM, HOST_PLATFORMS)
    ),
    HOST_PLATFORM_EXPLICIT=Static(bool(HOST_PLATFORM)),
    HOST_ARCH=Static(check(HOST_ARCH, HOST_ARCHS)),
    HOST_BITS=Static(check(HOST_BITS, HOST_TARGET_BITS)),
    TARGET_PLATFORM=Static(check(TARGET_PLATFORM, TARGET_PLATFORMS)),
    TARGET_ARCH=Static(check(TARGET_ARCH, TARGET_ARCHS)),
    TARGET_BITS=Static(check(TARGET_BITS, HOST_TARGET_BITS)),
    TARGET_CROS_BOARDS=Static(TARGET_CROS_BOARDS),
    CROS_BOARDS_WITH_QEMU_IMAGES=Static(CROS_BOARDS_WITH_QEMU_IMAGES),
    TEST_ONLY=Static(TEST_ONLY),
    gn_args=List(str),
    clobber_before_runhooks=Single(
      bool, empty_val=False, required=False, hidden=False
    ),
  )


def get_expected_host_platform(c):
  """Get the expected compiling platform for a config"""
  return {
    'android': 'linux',
    'chromeos': 'linux',
    'fuchsia': 'linux',
    'ios': 'mac',
  }.get(c.TARGET_PLATFORM, c.TARGET_PLATFORM)


def validate_config(c, actual_host_platform):
  """Validate the configuration.

  This will be called after chromium.{set,make,apply}_config to validate
  the config object.
  """
  if c.HOST_PLATFORM != actual_host_platform:
    raise BadConf(
      f'Current platform: {actual_host_platform} does not match '
      f'expected platform: {c.HOST_PLATFORM}'
    )
  host_targ_tuples = [
    (c.HOST_PLATFORM, c.HOST_ARCH, c.HOST_BITS),
    (c.TARGET_PLATFORM, c.TARGET_ARCH, c.TARGET_BITS),
  ]

  for plat, arch, bits in host_targ_tuples:
    if plat == 'ios':
      if arch not in ('arm', 'intel'):  # pragma: no cover
        raise BadConf('%s/%s arch is not supported on %s' % (arch, bits, plat))
    elif plat == 'mac':
      if not (
        arch == 'intel' or (arch == 'arm' and bits == 64)
      ):  # pragma: no cover
        raise BadConf('%s/%s arch is not supported on %s' % (arch, bits, plat))
    elif plat == 'win':
      if arch not in ('arm', 'intel'):  # pragma: no cover
        raise BadConf('%s arch is not supported on %s' % (arch, plat))
    elif plat in ('chromeos', 'android', 'linux', 'fuchsia'):
      pass  # no arch restrictions
    else:  # pragma: no cover
      assert False, "Not covering a platform: %s" % plat

  # TEST_ONLY builders are not compiling that are not limited by host platforms
  # listed in the potential_platforms.
  if not c.TEST_ONLY:
    # Configs have assumed platforms based on their target. Only accept
    # deviations when the host_platform is set explicitly in the chromium config
    # This requires builders that are "cross compiling" to set their
    # host_platform
    expected_compiler_platform = get_expected_host_platform(c)
    if (
      not c.HOST_PLATFORM_EXPLICIT
      and c.HOST_PLATFORM != expected_compiler_platform
    ):
      raise BadConf(
        f'Unexpectedly attempting to compile {c.TARGET_PLATFORM} '
        f'from {c.HOST_PLATFORM}. The chromium config '
        'HOST_PLATFORM was not set explicitly'
      )

    potential_platforms = {
      # host -> potential target platforms
      'win': ('win',),
      'mac': ('mac', 'ios'),
      'linux': ('linux', 'chromeos', 'android', 'fuchsia', 'mac', 'win'),
    }.get(c.HOST_PLATFORM)

    if not potential_platforms:  # pragma: no cover
      raise BadConf('Cannot build on "%s"' % c.HOST_PLATFORM)

    if c.TARGET_PLATFORM not in potential_platforms:
      raise BadConf(
        'Can not compile "%s" on "%s"' % (c.TARGET_PLATFORM, c.HOST_PLATFORM)
      )  # pragma: no cover

  if c.TARGET_CROS_BOARDS:
    if not c.TARGET_PLATFORM == 'chromeos':  # pragma: no cover
      raise BadConf("Cannot specify CROS boards for non-'chromeos' platform")

  if c.CROS_BOARDS_WITH_QEMU_IMAGES:
    if not c.TARGET_PLATFORM == 'chromeos':  # pragma: no cover
      raise BadConf(
        "Cannot specify CROS_BOARDS_WITH_QEMU_IMAGES for "
        "non-'chromeos' platform"
      )

  if c.HOST_BITS < c.TARGET_BITS:
    raise BadConf('host bits < targ bits')  # pragma: no cover

  if c.project_generator.tool not in PROJECT_GENERATORS:  # pragma: no cover
    raise BadConf(
      '"%s" is not a supported project generator tool, the '
      'supported ones are: %s'
      % (c.project_generator.tool, ','.join(PROJECT_GENERATORS))
    )

  if c.BUILD_CONFIG not in BUILD_CONFIGS:  # pragma: no cover
    raise BadConf('Unknown build config "%s"' % c.BUILD_CONFIG)


config_ctx = config_item_context(BaseConfig)


@config_ctx(is_root=True)
def BASE(c):
  c.build_config_fs = c.BUILD_CONFIG
  if c.HOST_PLATFORM == 'win':
    if c.TARGET_BITS == 64 and c.TARGET_ARCH != 'arm':
      # Windows requires x64 builds to be in <dir>_x64. (not true at this
      # point?)
      # TODO(dpranke): Figure out if we should use the '_x64' thing to
      # consistent w/ GYP, or drop it to be consistent w/ the other platforms.
      c.build_config_fs = c.BUILD_CONFIG + '_x64'

  c.targets_spec_dir = 'testing/buildbot'
  c.analyze_config_path = 'testing/buildbot/trybot_analyze_config.json'
  # Test runner memory tools that are not compile-time based.

  if c.HOST_PLATFORM == 'mac':
    c.mac_toolchain.installer_cipd_package = (
      'infra/tools/mac_toolchain/${platform}'
    )
    c.mac_toolchain.installer_version = (
      'git_revision:07e67ff89ff11ed0e3071867ed6bb49319a91b05'
    )
    c.mac_toolchain.installer_cmd = 'mac_toolchain'

  # TODO(sergeyberezin): remove this when all builds switch to the new Xcode
  # flow.
  if c.TARGET_PLATFORM == 'mac':
    c.env.FORCE_MAC_TOOLCHAIN = 1


@config_ctx()
def gn(c):
  c.project_generator.tool = 'gn'


@config_ctx()
def mb(c):
  c.project_generator.tool = 'mb'
  c.project_generator.use_luci_auth = True


@config_ctx()
def mb_no_luci_auth(c):
  c.project_generator.use_luci_auth = False


@config_ctx()
def mb_allow_dup_isolate_entry(c):
  """This config allows duplicated isolate entries from isolate map files.
  Should only be used during a migration from multiple isolate map files to a
  single starlark-generated isolate map file."""
  c.project_generator.allow_dup_isolate_entry = True


@config_ctx(group='builder')
def ninja(c):
  pass


@config_ctx(group='builder')
def xcode(c):  # pragma: no cover
  if c.HOST_PLATFORM != 'mac':
    raise BadConf('can not use xcodebuild on "%s"' % c.HOST_PLATFORM)


def _clang_common(c):
  c.compile_py.compiler = 'clang'
  c.gn_args.append('is_clang=true')
  c.gyp_env.GYP_DEFINES['clang'] = 1  # Read by api.py.


def _rust_common(c):
  c.gyp_env.GYP_DEFINES['rust'] = 1  # Read by api.py.


@config_ctx(group='compiler')
def clang(c):
  _clang_common(c)


@config_ctx(group='compiler')
def gcc(c):
  c.gn_args.append('is_clang=false')
  c.gyp_env.GYP_DEFINES['clang'] = 0  # Read by api.py.


@config_ctx(group='compiler')
def default_compiler(c):
  if c.TARGET_PLATFORM in ('mac', 'ios'):
    _clang_common(c)


@config_ctx()
def rust(c):
  _clang_common(c)
  _rust_common(c)


@config_ctx()
def dcheck(c, invert=False):
  c.gn_args.append('dcheck_always_on=%s' % str(not invert).lower())


@config_ctx()
def fastbuild(c, invert=False):
  c.gn_args.append('symbol_level=%d' % (1 if invert else 2))


@config_ctx()
def clobber(c):
  c.clobber_before_runhooks = True


@config_ctx()
def shared_build_dir(c):
  c.shared_build_dir = True


@config_ctx(includes=['clobber'])
def official(c):
  c.compile_py.mode = 'official'
  c.cros_sdk.external = False


@config_ctx()
def official_no_clobber(c):
  c.compile_py.mode = 'official'
  c.cros_sdk.external = False


@config_ctx(includes=['mac_toolchain'])
# Common config for rust and clang tot bots. They need both the system xcode
# (from mac_toolchain) to build clang, and hermetic xcode to build chrome.
def _tot_mac_common(c):
  fastbuild(c, final=False)  # final=False so clang_tot_mac_asan can override.

  # The 'ios' kind includes both mac and ios sdks. The clang tot bots need
  # the iOS SDK to be able to build compiler-rt runtimes for both mac and ios.
  c.mac_toolchain.kind = 'ios'

  # Despite the tot bots needing system xcode for building clang, they still
  # also need hermetic xcode for building chrome.
  c.env.FORCE_MAC_TOOLCHAIN = 1


@config_ctx(deps=['compiler'])
def asan(c):
  if 'clang' not in c.compile_py.compiler:  # pragma: no cover
    raise BadConf('asan requires clang')
  c.runtests.enable_asan = True
  if c.TARGET_PLATFORM in ['mac', 'win']:
    # Set fastbuild=0 and prevent other configs from changing it.
    fastbuild(c, invert=True, optional=False)

  c.gn_args.append('is_asan=true')
  if c.TARGET_PLATFORM not in ('android', 'mac') and c.TARGET_BITS == 64:
    # LSAN isn't supported on Android, Mac or 32 bits platforms.
    c.gn_args.append('is_lsan=true')


@config_ctx(deps=['compiler'])
def lsan(c):
  c.runtests.enable_lsan = True


@config_ctx(deps=['compiler'])
def msan(c):
  if 'clang' not in c.compile_py.compiler:  # pragma: no cover
    raise BadConf('msan requires clang')
  c.runtests.enable_msan = True
  c.gn_args.append('is_msan=true')


@config_ctx(deps=['compiler'])
def ubsan(c):
  if 'clang' not in c.compile_py.compiler:  # pragma: no cover
    raise BadConf('ubsan requires clang')
  c.gn_args.append('is_ubsan=true')


@config_ctx(deps=['compiler'])
def ubsan_vptr(c):
  if 'clang' not in c.compile_py.compiler:  # pragma: no cover
    raise BadConf('ubsan_vptr requires clang')
  c.gn_args.append('is_ubsan_vptr=true')


@config_ctx(deps=['compiler'], group='memory_tool')
def tsan2(c):
  if 'clang' not in c.compile_py.compiler:  # pragma: no cover
    raise BadConf('tsan2 requires clang')
  c.runtests.enable_tsan = True
  c.gn_args.append('is_tsan=true')


@config_ctx()
def trybot_flavor(c):
  fastbuild(c, optional=True)
  dcheck(c, optional=True)


@config_ctx()
def clang_tot(c):
  c.use_tot_clang = True


# When using any of these configs that read files from src-internal, the gclient
# config 'chrome_internal' should also be used to ensure that the affected files
# can be correctly reported


@config_ctx()
def internal_isolate_paths(c):
  c.project_generator.isolate_map_paths = [
    'internal/infra/config/generated/testing/gn_isolate_map.pyl',
  ]


@config_ctx(includes=['internal_isolate_paths'])
def internal_targets_specs(c):
  c.targets_spec_dir = 'internal/testing/buildbot'
  c.analyze_config_path = 'internal/testing/buildbot/trybot_analyze_config.json'


@config_ctx(includes=['mb'])
def internal_mb_config(c):
  c.project_generator.config_path = 'internal/tools/mb/mb_config.pyl'


#### 'Full' configurations


@config_ctx(includes=['ninja', 'clang', 'asan'])
def win_asan(_):
  pass


@config_ctx(includes=['ninja', 'default_compiler'])
def chromium(c):
  c.compile_py.default_targets = ['all']
  c.cros_sdk.external = True


@config_ctx(includes=['ninja', 'clang'])
def chromium_win_clang(c):
  fastbuild(c, final=False)  # final=False so win_clang_asan can override it.


@config_ctx(includes=['ninja', 'clang', 'clang_tot'])
def chromium_win_clang_tot(c):
  fastbuild(c)


@config_ctx(includes=['ninja', 'rust'])
def chromium_win_rust_tot(c):
  fastbuild(c)


@config_ctx(includes=['chromium_win_clang', 'official'])
def chromium_win_clang_official(_):
  pass


@config_ctx(includes=['chromium_win_clang_tot', 'official'])
def chromium_win_clang_official_tot(_):
  pass


@config_ctx(includes=['win_asan'])
def chromium_win_clang_asan(_):
  pass


@config_ctx(includes=['win_asan', 'clang_tot'])
def chromium_win_clang_asan_tot(_):
  pass


@config_ctx(includes=['ninja', 'clang', 'clang_tot'])
def clang_tot_linux(_):
  pass


@config_ctx(includes=['ninja', 'rust'])
def rust_tot_linux(_):
  pass


@config_ctx(includes=['ninja', 'clang', 'clang_tot'])
def clang_tot_mac(c):
  _tot_mac_common(c)


@config_ctx(includes=['ninja', 'rust'])
def rust_tot_mac(c):
  _tot_mac_common(c)


@config_ctx(includes=['ninja', 'clang', 'clang_tot'])
def clang_tot_ios(c):
  fastbuild(c, final=False)  # final=False so clang_tot_mac_asan can override.


@config_ctx(includes=['clang_tot_linux', 'asan'])
def clang_tot_linux_asan(_):
  pass


@config_ctx(includes=['ninja', 'clang', 'clobber', 'ubsan'])
def chromium_linux_ubsan(_):
  pass


@config_ctx(includes=['ninja', 'clang', 'clobber', 'ubsan_vptr'])
def chromium_linux_ubsan_vptr(_):
  pass


@config_ctx(includes=['clang_tot_linux', 'ubsan_vptr'])
def clang_tot_linux_ubsan_vptr(_):
  pass


@config_ctx(includes=['clang_tot_mac', 'asan'])
def clang_tot_mac_asan(_):
  pass


@config_ctx(includes=['android_common', 'ninja', 'clang', 'clang_tot'])
def clang_tot_android(_):
  pass


@config_ctx(includes=['clang_tot_android', 'asan'])
def clang_tot_android_asan(_):
  # Like android_clang, minus static_libarary, plus asan.
  pass


@config_ctx(includes=['clang_tot_android'])
def clang_tot_android_dbg(_):
  # Like android_clang, minus static_libarary.
  pass


@config_ctx(includes=['ninja', 'clang', 'clang_tot'])
def clang_tot_chromeos(_):
  pass


@config_ctx(includes=['ninja', 'clang', 'clang_tot'])
def clang_tot_fuchsia(_):
  pass


# GYP_DEFINES must not include 'asan' or 'clang', else the tester bot will try
# to compile clang.
@config_ctx(includes=['chromium'])
def chromium_win_asan(c):
  c.runtests.run_asan_test = True


@config_ctx(includes=['ninja', 'clang', 'asan'])
def chromium_asan(c):  # pragma: no cover
  # Used by some bots in chromium_tests/chromium_fuzz.py.
  del c


@config_ctx(includes=['ninja', 'clang', 'msan'])
def chromium_msan(c):
  c.compile_py.default_targets = ['all']


@config_ctx(includes=['ninja', 'clang', 'tsan2'])
def chromium_tsan2(c):
  c.compile_py.default_targets = ['all']


@config_ctx(includes=['ninja', 'default_compiler'])
def chromium_chromeos(c):  # pragma: no cover
  c.compile_py.default_targets = ['all']


@config_ctx(includes=['ninja', 'clang'])
def chromium_chromeos_clang(c):  # pragma: no cover
  c.compile_py.default_targets = ['all']


@config_ctx(includes=['ninja', 'clang'])
def chromium_clang(c):
  c.compile_py.default_targets = ['all']


@config_ctx(includes=['android_common', 'ninja', 'default_compiler'])
def android(_):
  pass


@config_ctx(includes=['android_common', 'ninja', 'clang'])
def android_clang(_):
  pass


@config_ctx(includes=['android_common', 'ninja', 'clang', 'asan'])
def android_asan(_):
  pass


@config_ctx(includes=['chromium', 'mac_toolchain'])
def chromium_tvos(c):
  c.mac_toolchain.kind = 'tvos'


@config_ctx()
def android_common(c):
  c.env.PATH.extend(
    [
      'third_party/android_sdk/public/platform-tools',
      'build/android',
    ]
  )


@config_ctx(includes=['ninja', 'clang'])
def codesearch(c):
  # -k 0 prevents stopping on errors, so the compile step tries to do as much as
  # possible.
  c.compile_py.build_args = ['-k', '0']
  if c.TARGET_PLATFORM == 'mac':
    c.env.FORCE_MAC_TOOLCHAIN = 1

  if c.TARGET_PLATFORM == 'ios':
    c.mac_toolchain.enabled = True
    c.env.FORCE_MAC_TOOLCHAIN = 0


@config_ctx()
def download_xr_test_apks(c):
  c.gyp_env.DOWNLOAD_XR_TEST_APKS = 1


@config_ctx()
def mac_toolchain(c):
  # Check for c.HOST_PLATFORM = mac is done in recipe
  c.mac_toolchain.enabled = True
  # TODO(crbug.com/797051): remove this when all builds switch to the new Xcode
  # flow.
  c.env.FORCE_MAC_TOOLCHAIN = 0


@config_ctx(includes=['mb'])
def android_internal_isolate_maps(c):
  c.project_generator.isolate_map_paths = [
    'infra/config/generated/testing/gn_isolate_map.pyl',
    'clank/build/bot/gn_isolate_map.pyl',
  ]
