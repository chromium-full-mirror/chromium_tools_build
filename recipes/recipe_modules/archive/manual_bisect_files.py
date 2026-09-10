# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Defines variables necessary to make lightweight linux perf builds.

Declares required files and whitelisted files to run manual bisect
script on perf builds. Binary files that should be
stripped to reduce zip file size are declared. The file list was
gotten from the local chrome executable path. (This can be retrieved by
typing 'chrome://version' in chrome and following the executable path.
The list needs to be updated if future chrome versions require additional files.
"""

from __future__ import annotations


CHROME_REQUIRED_FILES = {
  'android': [
    'apks',
    'apks/*',
  ],
  'linux': [
    'chrome',
    'chrome_100_percent.pak',
    'chrome_200_percent.pak',
    'chrome_crashpad_handler',
    'chromedriver',
    'default_apps',
    'default_apps/*',
    'icudtl.dat',
    'libEGL.so',
    'libGLESv2.so',
    'libclearkeycdm.so',
    'libclearkeycdmadapter.so',
    'libvk_swiftshader.so',
    'libvulkan.so.1',
    'libvulkan_init.so',
    'libvulkan_init_skia.so',
    'libvulkan_interface_shared.so',
    'libvulkan_wrapper.so',
    'libwidevinecdm.so',
    'libwidevinecdmadapter.so',
    'locales',
    'locales/*',
    'product_logo_48.png',
    'resources',
    'resources/*',
    'resources.pak',
    'v8_context_snapshot.bin',
    'vk_swiftshader_icd.json',
  ],
  'win': [
    'chrome.dll',
    'chrome.exe',
    'chrome_100_percent.pak',
    'chrome_200_percent.pak',
    'chrome_child.dll',
    'chrome_elf.dll',
    'chrome_watcher.dll',
    'chromedriver.exe',
    'default_apps',
    'default_apps/*',
    'd3dcompiler_47.dll',
    'dxcompiler.dll',
    'dxil.dll',
    'icudtl.dat',
    'libEGL.dll',
    'libGLESv2.dll',
    'locales',
    'locales/*',
    'resources.pak',
    'SecondaryTile.png',
    'v8_context_snapshot.bin',
    'vk_swiftshader.dll',
    'vk_swiftshader_icd.json',
    'vulkan-1.dll',
    'WidevineCdm',
    'WidevineCdm/*',
  ],
  'mac': [
    'chromedriver',
    'Google Chrome.app',
    'Google Chrome.app/*',
  ],
  'fuchsia': [
    'chromedriver',
    'gen',
    'gen/chrome',
    'gen/chrome/app',
    'gen/chrome/app/chrome',
    'gen/chrome/app/chrome/chrome.far',
    'gen/fuchsia_web',
    'gen/fuchsia_web/runners',
    'gen/fuchsia_web/runners/cast_runner',
    'gen/fuchsia_web/runners/cast_runner/cast_runner.far',
    'gen/fuchsia_web/webengine',
    'gen/fuchsia_web/webengine/web_engine',
    'gen/fuchsia_web/webengine/web_engine/web_engine.far',
    'gen/fuchsia_web/shell',
    'gen/fuchsia_web/shell/web_engine_shell',
    'gen/fuchsia_web/shell/web_engine_shell/web_engine_shell.far',
  ],
  # As of 2022/2, this only represents Lacros.
  'chromeos': [
    'chrome',
    'chrome_100_percent.pak',
    'chrome_200_percent.pak',
    'chrome_crashpad_handler',
    'headless_lib_data.pak',
    'headless_lib_strings.pak',
    'icudtl.dat',
    'metadata.json',
    'resources.pak',
    'snapshot_blob.bin',
    'locales',
    'locales/*',
    'swiftshader',
    'swiftshader/*',
    'WidevineCdm',
    'WidevineCdm/*',
  ],
}

CHROME_WHITELIST_FILES = {
  'linux': '',
  'win': '^\d+\.\d+\.\d+\.\d+\.manifest$',
  'mac': '',
}

CHROME_STRIP_LIST = {
  'linux': [
    'chrome',
    'chromedriver',
  ],
  'win': [
    # No stripping symbols from win64 archives.
  ],
  'mac': [
    # No stripping symbols from Mac archives.
  ],
}
