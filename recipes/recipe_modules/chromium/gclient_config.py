# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from . import reclient

from RECIPE_MODULES.depot_tools.gclient import CONFIG_CTX
from RECIPE_MODULES.depot_tools.gclient import api as gclient_api
from RECIPE_MODULES.depot_tools.gclient.config import (
  ChromiumGitURL, ChromeInternalGitURL)

def mirror_only(c, obj, default=None):
  return obj if c.USE_MIRROR else (default or obj.__class__())

@CONFIG_CTX()
def chromium_bare(c):
  s = c.solutions.add()
  s.name = 'src'
  s.url = ChromiumGitURL(c, 'chromium', 'src.git')
  s.custom_vars = {
      # We always want the bots to fetch the dependencies needed to
      # run the telemetry tests, regardless of whether they are needed or not
      # (this makes things simpler and more consistent).
      'checkout_telemetry_dependencies': 'True'
  }
  m = c.got_revision_reverse_mapping
  m['got_revision'] = 'src'
  m['got_angle_revision'] = 'src/third_party/angle'
  m['got_buildtools_revision'] = 'src/buildtools'
  m['got_dawn_revision'] = 'src/third_party/dawn'
  m['got_nacl_revision'] = 'src/native_client'
  m['got_swiftshader_revision'] = 'src/third_party/swiftshader'
  m['got_v8_revision'] = 'src/v8'
  m['got_webrtc_revision'] = 'src/third_party/webrtc'

  p = c.parent_got_revision_mapping
  p['parent_got_revision'] = None
  p['parent_got_angle_revision'] = 'angle_revision'
  p['parent_got_dawn_revision'] = 'dawn_revision'
  p['parent_got_nacl_revision'] = 'nacl_revision'
  p['parent_got_swiftshader_revision'] = 'swiftshader_revision'
  p['parent_got_v8_revision'] = 'v8_revision'
  p['parent_got_webrtc_revision'] = 'webrtc_revision'

  p = c.repo_path_map
  p['https://chromium.googlesource.com/chromium/src'] = ('src', None)
  p['https://chromium.googlesource.com/angle/angle'] = (
      'src/third_party/angle', None)
  p['https://dawn.googlesource.com/dawn'] = (
      'src/third_party/dawn', None)
  p['https://chromium.googlesource.com/chromium/buildtools'] = (
      'src/buildtools', 'HEAD')
  p['https://chromium.googlesource.com/catapult'] = (
      'src/third_party/catapult', 'HEAD')
  p['https://chromium.googlesource.com/chromium/deps/flac'] = (
      'src/third_party/flac', 'HEAD')
  p['https://chromium.googlesource.com/chromium/deps/icu'] = (
      'src/third_party/icu', 'HEAD')
  p['https://chromium.googlesource.com/devtools/devtools-frontend'] = (
      'src/third_party/devtools-frontend/src', 'HEAD')
  p['https://pdfium.googlesource.com/pdfium'] = (
      'src/third_party/pdfium', 'HEAD')
  p['https://skia.googlesource.com/skia'] = ('src/third_party/skia', 'HEAD')
  p['https://chromium.googlesource.com/v8/v8'] = ('src/v8', 'HEAD')
  p['https://webrtc.googlesource.com/src'] = ('src/third_party/webrtc', 'HEAD')
  # TODO(https://crbug.com/swiftshader/164): Change to main once created.
  p['https://swiftshader.googlesource.com/SwiftShader/'] = (
      'src/third_party/swiftshader', 'refs/heads/master')


@CONFIG_CTX(includes=['chromium_bare'])
def chromium_empty(c):
  c.solutions[0].deps_file = ''  # pragma: no cover

@CONFIG_CTX(includes=['chromium_bare'])
def chromium(c):
  s = c.solutions[0]
  s.custom_deps = mirror_only(c, {})

@CONFIG_CTX(includes=['chromium'])
def chromium_lkgr(c):
  s = c.solutions[0]
  s.revision = 'origin/lkgr'

@CONFIG_CTX(includes=['chromium_bare'])
def android_bare(c):
  # We inherit from chromium_bare to get the got_revision mapping.
  # NOTE: We don't set a specific got_revision mapping for src/repo.
  del c.solutions[0]
  c.got_revision_reverse_mapping['got_src_revision'] = 'src'
  del c.got_revision_reverse_mapping['got_revision']
  s = c.solutions.add()
  s.deps_file = '.DEPS.git'

@CONFIG_CTX(includes=['chromium'])
def blink(c):
  c.solutions[0].revision = 'HEAD'
  del c.solutions[0].custom_deps
  c.revisions['src/third_party/WebKit'] = 'HEAD'

# TODO(phajdan.jr): Move to proper repo and add coverage.
@CONFIG_CTX(includes=['chromium'])
def blink_merged(c):  # pragma: no cover
  c.solutions[0].url = \
      'https://chromium.googlesource.com/playground/chromium-blink-merge.git'

@CONFIG_CTX(includes=['chromium'])
def ios(c):
  c.target_os.add('ios')

@CONFIG_CTX(includes=['chromium'])
def show_v8_revision(c):
  # Have the V8 revision appear in the web UI instead of Chromium's.
  c.got_revision_reverse_mapping['got_cr_revision'] = 'src'
  c.got_revision_reverse_mapping['got_revision'] = 'src/v8'
  # TODO(machenbach): Retain old behavior for now  and switch in separate CL.
  del c.got_revision_reverse_mapping['got_v8_revision']
  # Needed to get the testers to properly sync the right revision.
  c.parent_got_revision_mapping['parent_got_revision'] = 'got_revision'

@CONFIG_CTX(includes=['chromium'])
def v8_canary(c):
  c.revisions['src/v8'] = 'origin/canary'

@CONFIG_CTX(includes=['chromium'])
def v8_tot(c):
  c.revisions['src/v8'] = 'HEAD'

@CONFIG_CTX()
def chromeos(c):
  c.target_os.add('chromeos')

@CONFIG_CTX()
def fuchsia(c):
  c.target_os.add('fuchsia')

@CONFIG_CTX(includes=['fuchsia'])
def fuchsia_arm64(c):
  """Downloads terminal boot images for running ARM64 binaries on QEMU."""

  c.solutions[0].custom_vars['checkout_fuchsia_boot_images'] = 'qemu.arm64'

@CONFIG_CTX(includes=['fuchsia'])
def fuchsia_x64(c):
  """Downloads terminal boot images for running x64 binaries on QEMU."""

  c.solutions[0].custom_vars['checkout_fuchsia_boot_images'] = 'qemu.x64'

@CONFIG_CTX(includes=['fuchsia'])
def fuchsia_no_hooks(c):
  """Downloads Fuchsia SDK without running hooks."""

  c.solutions[0].custom_vars['checkout_fuchsia_no_hooks'] = 'True'


@CONFIG_CTX(includes=['fuchsia'])
def fuchsia_arm64_host(c):
  """Downloads tools for running fuchsia emu on linux-arm64 host"""

  c.solutions[0].custom_vars['checkout_fuchsia_for_arm64_host'] = 'True'

@CONFIG_CTX(includes=['fuchsia'])
def fuchsia_internal(c):
  c.solutions[0].custom_vars['checkout_fuchsia_internal'] = 'True'


@CONFIG_CTX(includes=['fuchsia_internal'])
def fuchsia_astro_image(c):
  c.solutions[0].custom_vars['checkout_fuchsia_internal_images'] = (
      'smart_display_eng_arrested.astro-release')


@CONFIG_CTX(includes=['fuchsia_internal'])
def fuchsia_sd_images(c):
  c.solutions[0].custom_vars['checkout_fuchsia_internal_images'] = (
      'smart_display_eng_arrested.astro-release,'
      'smart_display_max_eng_arrested.sherlock-release,'
      'smart_display_m3_eng_paused.nelson-release')


@CONFIG_CTX(includes=['fuchsia_internal'])
def fuchsia_sherlock_image(c):
  c.solutions[0].custom_vars['checkout_fuchsia_internal_images'] = (
      'smart_display_max_eng_arrested.sherlock-release')


@CONFIG_CTX(includes=['fuchsia_x64'])
def fuchsia_workstation(c):
  """Downloads workstation boot images for running x64 binaries on QEMU."""

  c.solutions[0].custom_vars[
      'checkout_fuchsia_boot_images'] = 'workstation_eng.qemu-x64-release'


@CONFIG_CTX(includes=['fuchsia'])
def fuchsia_atlas(c):
  c.solutions[0].custom_vars['checkout_fuchsia_boot_images'] = (
      'workstation_eng.chromebook-x64-release')


@CONFIG_CTX(includes=['fuchsia'])
def fuchsia_workstation_perf_images(c):
  c.solutions[0].custom_vars['checkout_fuchsia_boot_images'] = (
      'workstation_eng.chromebook-x64,'
      'workstation_eng.x64')


@CONFIG_CTX()
def win(c):
  c.target_os.add('win')

@CONFIG_CTX(includes=['chrome_internal'])
def perf(c):
  s = c.solutions[0]
  s.managed = False
  needed_components_internal = [
    "src/data/page_cycler",
  ]
  for key in needed_components_internal:
    s.custom_deps.pop(key, None)

@CONFIG_CTX(includes=['chrome_internal'])
def chromium_perf(c):
  c.solutions[0].custom_vars['checkout_mobile_internal'] = 'True'

@CONFIG_CTX(includes=['chromium'])
def chromium_skia(c):
  c.solutions[0].revision = 'HEAD'
  del c.solutions[0].custom_deps
  c.revisions['src/third_party/skia'] = (
      gclient_api.RevisionFallbackChain('origin/main'))
  c.got_revision_reverse_mapping['got_chromium_revision'] = 'src'
  c.got_revision_reverse_mapping['got_revision'] = 'src/third_party/skia'
  c.parent_got_revision_mapping['parent_got_revision'] = 'got_revision'

@CONFIG_CTX(includes=['chromium'])
def chromium_webrtc(c):
  c.got_revision_reverse_mapping['got_libvpx_revision'] = (
      'src/third_party/libvpx/source')

@CONFIG_CTX(includes=['chromium_no_telemetry_dependencies'])
def chromium_webrtc_tot(c):
  """Configures WebRTC ToT revision for Chromium src/third_party/webrtc.

  Sets up ToT instead of the DEPS-pinned revision for WebRTC.
  This is used for some bots to provide data about which revisions are green to
  roll into Chromium.
  """
  c.revisions['src'] = 'HEAD'
  c.revisions['src/third_party/webrtc'] = 'HEAD'

  c.got_revision_reverse_mapping['got_libvpx_revision'] = (
      'src/third_party/libvpx/source')

  # Have the WebRTC revision appear in the web UI instead of Chromium's.
  c.got_revision_reverse_mapping['got_cr_revision'] = 'src'
  c.got_revision_reverse_mapping['got_revision'] = 'src/third_party/webrtc'
  # TODO(machenbach): Retain old behavior for now and switch in separate CL.
  del c.got_revision_reverse_mapping['got_webrtc_revision']

  # Needed to get the testers to properly sync the right revision.
  c.parent_got_revision_mapping['parent_got_revision'] = 'got_revision'
  c.parent_got_revision_mapping['parent_got_webrtc_revision'] = (
      'got_webrtc_revision')

@CONFIG_CTX()
def webrtc_test_resources(c):
  """Add webrtc.DEPS solution for test resources and tools.

  The webrtc.DEPS solution pulls in additional resources needed for running
  WebRTC-specific test setups in Chromium.
  """
  s = c.solutions.add()
  s.name = 'webrtc.DEPS'
  s.url = 'https://webrtc.googlesource.com/webrtc.DEPS'
  s.deps_file = 'DEPS'

@CONFIG_CTX(includes=['chromium'])
def chromedriver(c):
  """Add Selenium Java tests to the gclient solution."""
  c.solutions[0].custom_deps[
      'src/chrome/test/chromedriver/third_party/java_tests'] = (
          ChromiumGitURL(c, 'chromium', 'deps', 'webdriver'))

# TODO(phajdan.jr): Move to proper repo and add coverage.
@CONFIG_CTX()
def angle_top_of_tree(c):  # pragma: no cover
  """Configures the top-of-tree ANGLE in a Chromium checkout.

  Sets up ToT instead of the DEPS-pinned revision for ANGLE.
  """
  # TODO(tandrii): I think patch_projects in bare_chromium fixed this.
  c.revisions['src/third_party/angle'] = 'HEAD'

@CONFIG_CTX()
def dawn_top_of_tree(c):  # pragma: no cover
  """Configures the top-of-tree Dawn in a Chromium checkout.

  Sets up ToT instead of the DEPS-pinned revision for Dawn.
  """
  c.revisions['src/third_party/dawn'] = 'HEAD'


@CONFIG_CTX()
def swiftshader_top_of_tree(c):  # pragma: no cover
  """Configures the top-of-tree SwiftShader in a Chromium checkout.

  Sets up ToT instead of the DEPS-pinned revision for SwiftShader.
  """
  # TODO(https://crbug.com/swiftshader/164): Change to main once created.
  c.revisions['src/third_party/swiftshader'] = 'refs/heads/master'

# TODO(phajdan.jr): Move to proper repo and add coverage.
@CONFIG_CTX()
def valgrind(c):  # pragma: no cover
  """Add Valgrind binaries to the gclient solution."""
  c.solutions[0].custom_deps['src/third_party/valgrind'] = \
    ChromiumGitURL(c, 'chromium', 'deps', 'valgrind', 'binaries')

@CONFIG_CTX()
def ndk_next(c):
  c.revisions['src/third_party/android_ndk'] = 'origin/next'

@CONFIG_CTX()
def angle_internal(c):  # pragma: no cover
  # ANGLE only pulls a few non-public test files, like
  # GLES 1.0 conformance, and third party captures
  c.solutions[0].custom_vars['checkout_angle_internal'] = 'True'

@CONFIG_CTX(includes=['chromium'])
def chrome_internal(c):
  c.solutions[0].custom_vars['checkout_src_internal'] = 'True'
  # Remove some things which are generally not needed
  c.solutions[0].custom_deps = {
      "src/data/autodiscovery": None,
      "src/data/page_cycler": None,
      "src/tools/grit/grit/test/data": None,
      "src/chrome/test/data/perf/frame_rate/private": None,
      "src/data/mozilla_js_tests": None,
      "src/chrome/test/data/firefox2_profile/searchplugins": None,
      "src/chrome/test/data/firefox2_searchplugins": None,
      "src/chrome/test/data/firefox3_profile/searchplugins": None,
      "src/chrome/test/data/firefox3_searchplugins": None,
      "src/chrome/test/data/ssl/certs": None,
      "src/data/mach_ports": None,
      "src/data/esctf": None,
      "src/data/selenium_core": None,
      "src/chrome/test/data/plugin": None,
      "src/data/memory_test": None,
      "src/data/tab_switching": None,
      "src/chrome/test/data/osdd": None,
      "src/webkit/data/bmp_decoder": None,
      "src/webkit/data/ico_decoder": None,
      "src/webkit/data/test_shell/plugins": None,
      "src/webkit/data/xbm_decoder": None,
  }

  m = c.got_revision_reverse_mapping
  m['got_src_internal_revision'] = 'src-internal'

  p = c.repo_path_map
  p['https://chrome-internal.googlesource.com/chrome/src-internal'] = (
      'src-internal', 'HEAD')


@CONFIG_CTX()
def checkout_instrumented_libraries(c):
  c.solutions[0].custom_vars['checkout_instrumented_libraries'] = 'True'

@CONFIG_CTX(includes=['chromium'])
def chromium_no_telemetry_dependencies(c):  # pragma: no cover
  c.solutions[0].custom_vars['checkout_telemetry_dependencies'] = 'False'


@CONFIG_CTX(includes=['chromium'])
def chromium_skip_wpr_archives_download(c):
  c.solutions[0].custom_vars['skip_wpr_archives_download'] = 'True'


@CONFIG_CTX()
def android_prebuilts_build_tools(c):
  c.solutions[0].custom_vars['checkout_android_prebuilts_build_tools'] = 'True'

@CONFIG_CTX()
def arm(c):
  c.target_cpu.add('arm')

@CONFIG_CTX()
def arm64(c):
  c.target_cpu.add('arm64')

@CONFIG_CTX()
def use_clang_coverage(c):
  c.solutions[0].custom_vars['checkout_clang_coverage_tools'] = 'True'


@CONFIG_CTX()
def enable_wpr_tests(c):
  c.solutions[0].custom_vars['checkout_wpr_archives'] = 'True'


# Official builders are required to checkout pgo profiles.
@CONFIG_CTX()
def checkout_pgo_profiles(c):
  c.solutions[0].custom_vars['checkout_pgo_profiles'] = 'True'


# Lacros builders are required to checkout Lacros sdks.
@CONFIG_CTX()
def checkout_lacros_sdk(c):
  c.solutions[0].custom_vars['checkout_lacros_sdk'] = 'True'


@CONFIG_CTX()
def checkout_bazel(c):
  c.solutions[0].custom_vars['checkout_bazel'] = 'True'


@CONFIG_CTX()
def use_clang_tidy(c):
  c.solutions[0].custom_vars['checkout_clang_tidy'] = 'True'

@CONFIG_CTX()
def clang_tot(c):
  c.solutions[0].custom_vars['llvm_force_head_revision'] = 'True'

@CONFIG_CTX(includes=['chromium'])
def openscreen_tot(c):
  c.revisions['src/third_party/openscreen/src'] = 'HEAD'

@CONFIG_CTX()
def ios_webkit_tot(c):
  c.solutions[0].custom_vars['checkout_ios_webkit'] = 'True'
  c.solutions[0].custom_vars['ios_webkit_revision'] = 'refs/heads/main'


@CONFIG_CTX()
def no_kaleidoscope(c):
  c.solutions[0].custom_vars['checkout_kaleidoscope'] = 'False'


@CONFIG_CTX()
def enable_soda_integration_tests(c):
  c.solutions[0].custom_vars['checkout_soda'] = 'True'


# This configuration overrides the default reclient version
# with the staging version.  This is used for testing new
# reclient releases.
@CONFIG_CTX()
def reclient_staging(c):
  cv = c.solutions[0].custom_vars
  cv['reclient_version'] = reclient.STAGING_VERSION


# This configuration overrides the default reclient version
# with the test version.  This is used for testing new
# reclient releases.
@CONFIG_CTX()
def reclient_test(c):
  cv = c.solutions[0].custom_vars
  cv['reclient_version'] = reclient.TEST_VERSION


# This configuration overrides the default reclient version
# with a clang-scan-deps version.  This is used for doing builds
# with the clang-scan-deps based input processor.
@CONFIG_CTX()
def reclient_clang_scan_deps(c):
  cv = c.solutions[0].custom_vars
  cv['reclient_version'] = reclient.CLANG_SCAN_DEPS_VERSION


@CONFIG_CTX()
def use_rust(c):
  c.solutions[0].custom_vars['use_rust'] = 'True'


@CONFIG_CTX()
def checkout_rust_toolchain_deps(c):
  c.solutions[0].custom_vars['checkout_rust_toolchain_deps'] = 'True'
