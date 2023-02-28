# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import pipes

from recipe_engine.config import config_item_context, ConfigGroup
from recipe_engine.config import Dict, List, Single, Static, Set, BadConf
from recipe_engine.config_types import Path

# Because of the way that we use decorators, pylint can't figure out the proper
# type signature of functions annotated with the @config_ctx decorator.
# pylint: disable=E1123

HOST_PLATFORMS = ('linux', 'win', 'mac')
TARGET_PLATFORMS = HOST_PLATFORMS + ('ios', 'android', 'chromeos', 'fuchsia')
HOST_TARGET_BITS = (32, 64)
HOST_ARCHS = ('intel', 'arm')
TARGET_ARCHS = HOST_ARCHS + ('mips', 'mipsel')
BUILD_CONFIGS = ('Release', 'Debug')
PROJECT_GENERATORS = ('gn', 'mb')


def check(val, potentials):
  assert val in potentials, (val, potentials)
  return val


# Schema for config items in this module.
# Note: The default values for these parameters are defined via recipe_magic in
# chromium/api.py:get_config_defaults().
def BaseConfig(HOST_PLATFORM, HOST_ARCH, HOST_BITS, TARGET_PLATFORM,
               TARGET_ARCH, TARGET_BITS, BUILD_CONFIG, TARGET_CROS_BOARDS,
               CROS_BOARDS_WITH_QEMU_IMAGES, CHECKOUT_PATH, TEST_ONLY,
               **_kwargs):
  equal_fn = lambda tup: ('%s=%s' % (tup[0], pipes.quote(str(tup[1]))))
  return ConfigGroup(
      compile_py=ConfigGroup(
          default_targets=Set(str),
          build_args=List(str),
          compiler=Single(str, required=False),
          mode=Single(str, required=False),
          goma_dir=Single(Path, required=False),
          goma_client_type=Single(str, required=False),
          goma_use_local=Single(bool, empty_val=False, required=False),
          show_ninja_stats=Single(bool, empty_val=False, required=False),
          goma_hermetic=Single(str, required=False),
          goma_failfast=Single(bool, empty_val=False, required=False),
          goma_max_active_fail_fallback_tasks=Single(
              int, empty_val=None, required=False),
          goma_enable_global_file_stat_cache=Single(
              bool, empty_val=False, required=False),
          goma_enable_cache_silo=Single(bool, empty_val=False, required=False),
          prune_venv=Single(bool, empty_val=False, required=False),
          reclient_deps_cache_by_step=Single(
              bool, empty_val=False, required=False),
      ),
      gyp_env=ConfigGroup(
          DOWNLOAD_VR_TEST_APKS=Single(int, required=False),
          GYP_DEFINES=Dict(equal_fn, ' '.join, (str, int, Path)),
      ),
      # This allows clients to opt out of using GYP variables in the
      # environment.
      # TODO(machenbach): This does not expand to Chromium's runtests yet.
      use_gyp_env=Single(bool, empty_val=True, required=False),
      env=ConfigGroup(
          PATH=List(Path),
          GOMA_RPC_EXTRA_PARAMS=Single(str, required=False),
          GOMA_ARBITRARY_TOOLCHAIN_SUPPORT=Single(str, required=False),
          GOMA_STORE_ONLY=Single(bool, empty_val=False, required=False),
          GOMA_DEPS_CACHE_MAX_PROTO_SIZE_IN_MB=Single(int, required=False),
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
          config_path=Single(Path),
          args=Set(str),
          isolate_map_paths=List(Path),
          # TODO(crbug.com/1060857): Remove this once swarming task templates
          # support command prefixes.
          use_luci_auth=Single(bool, empty_val=False, required=False),
      ),
      # build_dir is the full path to the "out" directory. Actual
      # gn/ninja/compile artifacts are located in
      # build_dir.join(build_config_fs).
      build_dir=Single(Path),
      cros_sdk=ConfigGroup(
          external=Single(bool, empty_val=True, required=False),
          args=List(str),
      ),
      runtests=ConfigGroup(
          enable_memcheck=Single(bool, empty_val=False, required=False),
          memory_tests_runner=Single(Path),
          enable_asan=Single(bool, empty_val=False, required=False),
          enable_lsan=Single(bool, empty_val=False, required=False),
          enable_msan=Single(bool, empty_val=False, required=False),
          enable_tsan=Single(bool, empty_val=False, required=False),
          run_asan_test=Single(bool, required=False),
      ),
      source_side_spec_dir=Single(Path),
      # Path to the analyze config file used for configuring ignores/exclusions
      # for analyze (e.g. //testing/buildbot/trybot_analyze_config.json). If not
      # provided, then trybot_analyze_config.json in source_side_spec_dir will
      # be used.
      analyze_config_path=Single(Path, required=False),
      use_tot_clang=Single(bool, empty_val=False, required=False),

      # Some platforms do not have a 1:1 correlation of BUILD_CONFIG to what is
      # passed as --target on the command line.
      build_config_fs=Single(str),
      BUILD_CONFIG=Static(check(BUILD_CONFIG, BUILD_CONFIGS)),
      HOST_PLATFORM=Static(check(HOST_PLATFORM, HOST_PLATFORMS)),
      HOST_ARCH=Static(check(HOST_ARCH, HOST_ARCHS)),
      HOST_BITS=Static(check(HOST_BITS, HOST_TARGET_BITS)),
      TARGET_PLATFORM=Static(check(TARGET_PLATFORM, TARGET_PLATFORMS)),
      TARGET_ARCH=Static(check(TARGET_ARCH, TARGET_ARCHS)),
      TARGET_BITS=Static(check(TARGET_BITS, HOST_TARGET_BITS)),
      TARGET_CROS_BOARDS=Static(TARGET_CROS_BOARDS),
      CROS_BOARDS_WITH_QEMU_IMAGES=Static(CROS_BOARDS_WITH_QEMU_IMAGES),
      CHECKOUT_PATH=Static(CHECKOUT_PATH),
      TEST_ONLY=Static(TEST_ONLY),
      gn_args=List(str),
      clobber_before_runhooks=Single(
          bool, empty_val=False, required=False, hidden=False),
  )


def validate_config(c):
  """Validate the configuration.

  This will be called after chromium.{set,make,apply}_config to validate
  the config object.
  """
  host_targ_tuples = [(c.HOST_PLATFORM, c.HOST_ARCH, c.HOST_BITS),
                      (c.TARGET_PLATFORM, c.TARGET_ARCH, c.TARGET_BITS)]

  for (plat, arch, bits) in host_targ_tuples:
    if plat == 'ios':
      if arch not in ('arm', 'intel'):  # pragma: no cover
        raise BadConf('%s/%s arch is not supported on %s' % (arch, bits, plat))
    elif plat == 'mac':
      if not (arch == 'intel' or
              (arch == 'arm' and bits == 64)):  # pragma: no cover
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
    potential_platforms = {
        # host -> potential target platforms
        'win': ('win',),
        'mac': ('mac', 'ios'),
        'linux': ('linux', 'chromeos', 'android', 'fuchsia', 'win'),
    }.get(c.HOST_PLATFORM)

    if not potential_platforms:  # pragma: no cover
      raise BadConf('Cannot build on "%s"' % c.HOST_PLATFORM)

    if c.TARGET_PLATFORM not in potential_platforms:
      raise BadConf('Can not compile "%s" on "%s"' %
                    (c.TARGET_PLATFORM, c.HOST_PLATFORM))  # pragma: no cover

  if c.TARGET_CROS_BOARDS:
    if not c.TARGET_PLATFORM == 'chromeos':  # pragma: no cover
      raise BadConf("Cannot specify CROS boards for non-'chromeos' platform")

  if c.CROS_BOARDS_WITH_QEMU_IMAGES:
    if not c.TARGET_PLATFORM == 'chromeos':  # pragma: no cover
      raise BadConf("Cannot specify CROS_BOARDS_WITH_QEMU_IMAGES for "
                    "non-'chromeos' platform")

  if c.HOST_BITS < c.TARGET_BITS:
    raise BadConf('host bits < targ bits')  # pragma: no cover

  if c.project_generator.tool not in PROJECT_GENERATORS:  # pragma: no cover
    raise BadConf('"%s" is not a supported project generator tool, the '
                  'supported ones are: %s' %
                  (c.project_generator.tool, ','.join(PROJECT_GENERATORS)))

  if c.BUILD_CONFIG not in BUILD_CONFIGS:  # pragma: no cover
    raise BadConf('Unknown build config "%s"' % c.BUILD_CONFIG)


config_ctx = config_item_context(BaseConfig)


@config_ctx(is_root=True)
def BASE(c):
  c.build_config_fs = c.BUILD_CONFIG
  if c.HOST_PLATFORM == 'win':
    if c.TARGET_BITS == 64:
      # Windows requires 64-bit builds to be in <dir>_x64.
      c.build_config_fs = c.BUILD_CONFIG + '_x64'

  # TODO(crbug.com/1420014) Once the downstream repos have been updated to set
  # analyze_config_path, set it here
  c.source_side_spec_dir = c.CHECKOUT_PATH.join('testing', 'buildbot')
  # Test runner memory tools that are not compile-time based.
  c.runtests.memory_tests_runner = c.CHECKOUT_PATH.join(
      'tools',
      'valgrind',
      'chrome_tests',
      platform_ext={
          'win': '.bat',
          'mac': '.sh',
          'linux': '.sh'
      })

  if c.HOST_PLATFORM == 'mac':
    c.mac_toolchain.installer_cipd_package = (
        'infra/tools/mac_toolchain/${platform}')
    c.mac_toolchain.installer_version = (
        'git_revision:248bbfc433c8e2fc4e3536d8f58477724ce7110d')
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


@config_ctx(group='builder')
def ninja(c):
  c.build_dir = c.CHECKOUT_PATH.join('out')


@config_ctx()
def goma_failfast(c):
  c.compile_py.goma_failfast = True


@config_ctx()
def goma_enable_global_file_stat_cache(c):
  # Do not enable this if some src files are modified for recompilation
  # while running goma daemon.
  c.compile_py.goma_enable_global_file_stat_cache = True


@config_ctx()
def goma_enable_cache_silo(c):
  # If enabled, the builder name will be used as the RBE cache silo key.
  c.compile_py.goma_enable_cache_silo = True


@config_ctx()
def goma_store_only(c):
  c.env.GOMA_STORE_ONLY = True


@config_ctx()
def goma_large_cache_file(c):
  c.env.GOMA_DEPS_CACHE_MAX_PROTO_SIZE_IN_MB = 256


@config_ctx()
def goma_canary(c):
  c.compile_py.goma_client_type = 'candidate'
  c.compile_py.goma_hermetic = 'error'
  c.compile_py.goma_failfast = True
  c.compile_py.show_ninja_stats = True


@config_ctx()
def goma_latest_client(c):
  c.compile_py.goma_client_type = 'latest'
  c.compile_py.goma_hermetic = 'error'
  c.compile_py.goma_failfast = True
  c.compile_py.show_ninja_stats = True


@config_ctx()
def goma_client_candidate(c):
  c.compile_py.goma_client_type = 'candidate'


@config_ctx()
def goma_hermetic_fallback(c):
  c.compile_py.goma_hermetic = 'fallback'


@config_ctx()
def goma_use_local(c):
  c.compile_py.goma_use_local = True


@config_ctx()
def reclient_deps_cache_by_step(c):
  c.compile_py.reclient_deps_cache_by_step = True


@config_ctx(group='builder')
def xcode(c):  # pragma: no cover
  if c.HOST_PLATFORM != 'mac':
    raise BadConf('can not use xcodebuild on "%s"' % c.HOST_PLATFORM)


def _clang_common(c):
  c.compile_py.compiler = 'clang'
  c.gn_args.append('is_clang=true')
  c.gyp_env.GYP_DEFINES['clang'] = 1  # Read by api.py.


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


@config_ctx(deps=['compiler', 'builder'], group='distributor')
def goma(c):
  if not c.compile_py.compiler:
    c.compile_py.compiler = 'goma'
  elif c.compile_py.compiler == 'clang':
    c.compile_py.compiler = 'goma-clang'
  else:  # pragma: no cover
    raise BadConf('goma config doesn\'t understand %s' % c.compile_py.compiler)

  if c.TARGET_PLATFORM == 'win' and c.compile_py.compiler != 'goma-clang':
    fastbuild(c)


@config_ctx()
def dcheck(c, invert=False):
  c.gn_args.append('dcheck_always_on=%s' % str(not invert).lower())


@config_ctx()
def fastbuild(c, invert=False):
  c.gn_args.append('symbol_level=%d' % (1 if invert else 2))


@config_ctx()
def clobber(c):
  c.clobber_before_runhooks = True


@config_ctx(includes=['clobber'])
def official(c):
  c.compile_py.mode = 'official'
  c.cros_sdk.external = False


@config_ctx()
def official_no_clobber(c):
  c.compile_py.mode = 'official'
  c.cros_sdk.external = False


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


@config_ctx(group='memory_tool')
def memcheck(c):
  c.runtests.enable_memcheck = True


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


#### 'Full' configurations


@config_ctx(includes=['ninja', 'clang', 'asan'])
def win_asan(_):
  pass


@config_ctx(includes=['ninja', 'default_compiler'])
def chromium_no_goma(c):
  c.compile_py.default_targets = ['all']


@config_ctx(includes=['ninja', 'default_compiler', 'goma'])
def chromium(c):
  c.compile_py.default_targets = ['all']
  c.cros_sdk.external = True


@config_ctx(includes=['ninja', 'clang', 'goma'])
def chromium_win_clang(c):
  fastbuild(c, final=False)  # final=False so win_clang_asan can override it.


@config_ctx(includes=['ninja', 'clang', 'clang_tot'])  # No goma.
def chromium_win_clang_tot(c):
  fastbuild(c)


@config_ctx(includes=['chromium_win_clang', 'official'])
def chromium_win_clang_official(_):
  pass


@config_ctx(includes=['chromium_win_clang_tot', 'official'])
def chromium_win_clang_official_tot(_):
  pass


@config_ctx(includes=['win_asan', 'goma'])
def chromium_win_clang_asan(_):
  pass


@config_ctx(includes=['win_asan', 'clang_tot'])  # No goma.
def chromium_win_clang_asan_tot(_):
  pass


@config_ctx(includes=['ninja', 'clang', 'clang_tot'])  # No goma.
def clang_tot_linux(_):
  pass


# mac_toolchain causes the bots to download system Xcode. The clang tot
# bots need system Xcode to build clang; hermetic Xcode isn't sufficient.
@config_ctx(includes=['ninja', 'clang', 'clang_tot',
                      'mac_toolchain'])  # No goma.
def clang_tot_mac(c):
  fastbuild(c, final=False)  # final=False so clang_tot_mac_asan can override.

  # The 'ios' kind includes both mac and ios sdks. The clang tot bots need
  # the iOS SDK to be able to build compiler-rt runtimes for both mac and ios.
  c.mac_toolchain.kind = 'ios'

  # Despite the tot bots needing system xcode for building clang, they still
  # also need hermetic xcode for building chrome.
  c.env.FORCE_MAC_TOOLCHAIN = 1


@config_ctx(includes=['ninja', 'clang', 'clang_tot'])  # No goma.
def clang_tot_ios(c):
  fastbuild(c, final=False)  # final=False so clang_tot_mac_asan can override.


@config_ctx(includes=['clang_tot_linux', 'asan'])
def clang_tot_linux_asan(_):
  pass


@config_ctx(includes=['ninja', 'clang', 'goma', 'clobber', 'ubsan'])
def chromium_linux_ubsan(_):
  pass


@config_ctx(includes=['ninja', 'clang', 'goma', 'clobber', 'ubsan_vptr'])
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
  # Like android_clang, minus goma, minus static_libarary, plus asan.
  pass


@config_ctx(includes=['clang_tot_android'])
def clang_tot_android_dbg(_):
  # Like android_clang, minus goma, minus static_libarary.
  pass


@config_ctx(includes=['ninja', 'clang', 'clang_tot'])
def clang_tot_chromeos(_):
  pass


@config_ctx(includes=['ninja', 'clang', 'clang_tot'])
def clang_tot_fuchsia(_):
  pass

# GYP_DEFINES must not include 'asan' or 'clang', else the tester bot will try
# to compile clang.
@config_ctx(includes=['chromium_no_goma'])
def chromium_win_asan(c):
  c.runtests.run_asan_test = True


@config_ctx(includes=['ninja', 'clang', 'goma', 'asan'])
def chromium_asan(c):  # pragma: no cover
  # Used by some bots in chromium_tests/chromium_fuzz.py.
  del c


@config_ctx(includes=['ninja', 'clang', 'goma', 'msan'])
def chromium_msan(c):
  c.compile_py.default_targets = ['all']


@config_ctx(includes=['ninja', 'clang', 'goma', 'tsan2'])
def chromium_tsan2(c):
  c.compile_py.default_targets = ['all']


@config_ctx(includes=['ninja', 'default_compiler', 'goma'])
def chromium_chromeos(c):  # pragma: no cover
  c.compile_py.default_targets = ['all']


@config_ctx(includes=['ninja', 'clang', 'goma'])
def chromium_chromeos_clang(c):  # pragma: no cover
  c.compile_py.default_targets = ['all']


@config_ctx(includes=['ninja', 'clang', 'goma'])
def chromium_clang(c):
  c.compile_py.default_targets = ['all']


@config_ctx(includes=['android_common', 'ninja', 'default_compiler', 'goma'])
def android(_):
  pass


@config_ctx(includes=['android_common', 'ninja', 'clang', 'goma'])
def android_clang(_):
  pass


@config_ctx(includes=['android_common', 'ninja', 'clang', 'goma', 'asan'])
def android_asan(_):
  pass


@config_ctx()
def android_common(c):
  c.env.PATH.extend([
      c.CHECKOUT_PATH.join('third_party', 'android_sdk', 'public',
                           'platform-tools'),
      c.CHECKOUT_PATH.join('build', 'android')
  ])


@config_ctx(includes=['ninja', 'clang', 'goma'])
def codesearch(c):
  # -k 0 prevents stopping on errors, so the compile step tries to do as much as
  # possible.
  c.compile_py.build_args = ['-k', '0']
  if c.TARGET_PLATFORM == 'mac':
    c.env.FORCE_MAC_TOOLCHAIN = 1


@config_ctx()
def download_vr_test_apks(c):
  c.gyp_env.DOWNLOAD_VR_TEST_APKS = 1


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
      c.CHECKOUT_PATH.join('clank', 'build', 'bot', 'gn_isolate_map.pyl'),
      c.CHECKOUT_PATH.join('testing', 'buildbot', 'gn_isolate_map.pyl'),
  ]
