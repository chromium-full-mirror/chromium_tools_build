# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from RECIPE_MODULES.depot_tools.gclient import CONFIG_CTX


def WebRTCGitURL(_c, *pieces):
  return '/'.join(('https://webrtc.googlesource.com',) + pieces)


@CONFIG_CTX(includes=['_webrtc'])
def webrtc(_):
  pass


@CONFIG_CTX(includes=['webrtc'])
def webrtc_fuchsia(c):
  c.target_os.add('fuchsia')


@CONFIG_CTX(includes=['webrtc'])
def webrtc_ios(c):
  # WebRTC for iOS depends on the src/third_party/openmax_dl in Chromium, which
  # is set to None for iOS. Because of this, sync Mac as well to get it.
  c.target_os.add('mac')
  c.target_os.add('ios')


@CONFIG_CTX(includes=['webrtc'])
def webrtc_linux_mac_crosscompile_arm64(c):
  # Compiling webrtc on Linux for mac requires fetching mac dependencies.
  c.target_os.add('mac')
  c.target_cpu.add('arm64')


@CONFIG_CTX(includes=['webrtc'])
def webrtc_use_clang_coverage(c):
  """Add llvm coverage tools (llvm-cov and llvm-profdata) to the solution."""
  c.solutions[0].custom_vars['checkout_clang_coverage_tools'] = 'True'


@CONFIG_CTX(includes=['webrtc'])
def webrtc_use_include_cleaner(c):
  """clang-include-cleaner is built as part of the "clangd" package."""
  c.solutions[0].custom_vars['checkout_clangd'] = 'True'


@CONFIG_CTX()
def _webrtc(c):
  """Add the main solution for WebRTC standalone builds.

  This needs to be in it's own configuration that is added first in the
  dependency chain. Otherwise the webrtc-limited solution will end up as the
  first solution in the gclient spec, which doesn't work.
  """
  s = c.solutions.add()
  s.name = 'src'
  s.url = WebRTCGitURL(c, 'src')
  s.deps_file = 'DEPS'
  c.got_revision_mapping['src'] = 'got_revision'
