# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, StepCommandContains

DEPS = [
  'archive',
  'chromium',
  'depot_tools/bot_update',
  'depot_tools/gclient',
  'recipe_engine/path',
  'recipe_engine/properties',
  'recipe_engine/runtime',
]


def RunSteps(api):
  api.gclient.set_config('chromium')

  bot_update_step = api.bot_update.ensure_checkout()

  api.archive.zip_and_upload_build(
      step_name='zip build',
      target=api.path.checkout_dir.joinpath('Release', 'out'),
      build_url=api.archive.legacy_upload_url('example_bucket',
                                              'extra_component'),
      build_revision='example_sha',
      package_dsym_files=True,
      exclude_files='example_exclude',
      exclude_perf_test_files=True,
      platform=api.properties['platform'],
      update_properties=bot_update_step.presentation.properties,
      store_by_hash=False)


def GenTests(api):

  yield api.test(
      'linux',
      api.properties(
          buildername='example_buildername', gs_acl='public', platform='linux'),
      api.post_process(StepCommandContains, 'zip build', [
          '--build-url',
          'gs://example_bucket/extra_component/example_buildername',
          '--build_revision',
          'example_sha',
          '--package-dsym-files',
          '--exclude-files',
          'example_exclude',
          '--gs-acl',
          'public',
          '--include-files',
          'chrome,chrome_100_percent.pak,chrome_200_percent.pak,chrome_crashpad_handler,chromedriver,default_apps,default_apps/*,icudtl.dat,libEGL.so,libGLESv2.so,libclearkeycdm.so,libclearkeycdmadapter.so,libvk_swiftshader.so,libvulkan.so.1,libvulkan_init.so,libvulkan_init_skia.so,libvulkan_interface_shared.so,libvulkan_wrapper.so,libwidevinecdm.so,libwidevinecdmadapter.so,locales,locales/*,nacl_helper,nacl_helper_bootstrap,nacl_helper_nonsfi,nacl_irt_x86_64.nexe,PepperFlash,PepperFlash/*,pnacl,pnacl/*,product_logo_48.png,resources,resources/*,resources.pak,v8_context_snapshot.bin,vk_swiftshader_icd.json,xdg-mime,xdg-settings',
          '--strip-files',
          'chrome,chromedriver,nacl_helper',
          '--exclude-extra',
          '--build_revision',
          '170242',
          '--json-urls',
          '/path/to/tmp/json',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'mac',
      api.properties(
          buildername='example_buildername', gs_acl='public', platform='mac'),
      api.post_process(StepCommandContains, 'zip build', [
          '--build-url',
          'gs://example_bucket/extra_component/example_buildername',
          '--build_revision',
          'example_sha',
          '--package-dsym-files',
          '--exclude-files',
          'example_exclude',
          '--gs-acl',
          'public',
          '--include-files',
          'chromedriver,Google Chrome.app,Google Chrome.app/*',
          '--exclude-extra',
          '--build_revision',
          '170242',
          '--json-urls',
          '/path/to/tmp/json',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'win',
      api.properties(
          buildername='example_buildername', gs_acl='public', platform='win'),
      api.post_process(StepCommandContains, 'zip build', [
          '--build-url',
          'gs://example_bucket/extra_component/example_buildername',
          '--build_revision',
          'example_sha',
          '--package-dsym-files',
          '--exclude-files',
          'example_exclude',
          '--gs-acl',
          'public',
          '--include-files',
          'chrome.dll,chrome.exe,chrome_100_percent.pak,chrome_200_percent.pak,chrome_child.dll,chrome_elf.dll,chrome_watcher.dll,chromedriver.exe,default_apps,default_apps/*,d3dcompiler_47.dll,dxcompiler.dll,dxil.dll,icudtl.dat,libEGL.dll,libGLESv2.dll,locales,locales/*,PepperFlash,PepperFlash/*,resources.pak,SecondaryTile.png,v8_context_snapshot.bin,WidevineCdm,WidevineCdm/*',
          '--whitelist',
          '^\\d+\\.\\d+\\.\\d+\\.\\d+\\.manifest$',
          '--exclude-extra',
          '--build_revision',
          '170242',
          '--json-urls',
          '/path/to/tmp/json',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'linux-experimental',
      api.runtime(is_experimental=True),
      api.properties(
          buildername='example_buildername', gs_acl='public', platform='linux'),
      api.post_process(StepCommandContains, 'zip build', [
          '--build-url',
          'gs://example_bucket/experimental/extra_component/example_buildername',
          '--build_revision',
          'example_sha',
          '--package-dsym-files',
          '--exclude-files',
          'example_exclude',
          '--gs-acl',
          'public',
          '--include-files',
          'chrome,chrome_100_percent.pak,chrome_200_percent.pak,chrome_crashpad_handler,chromedriver,default_apps,default_apps/*,icudtl.dat,libEGL.so,libGLESv2.so,libclearkeycdm.so,libclearkeycdmadapter.so,libvk_swiftshader.so,libvulkan.so.1,libvulkan_init.so,libvulkan_init_skia.so,libvulkan_interface_shared.so,libvulkan_wrapper.so,libwidevinecdm.so,libwidevinecdmadapter.so,locales,locales/*,nacl_helper,nacl_helper_bootstrap,nacl_helper_nonsfi,nacl_irt_x86_64.nexe,PepperFlash,PepperFlash/*,pnacl,pnacl/*,product_logo_48.png,resources,resources/*,resources.pak,v8_context_snapshot.bin,vk_swiftshader_icd.json,xdg-mime,xdg-settings',
          '--strip-files',
          'chrome,chromedriver,nacl_helper',
          '--exclude-extra',
          '--build_revision',
          '170242',
          '--json-urls',
          '/path/to/tmp/json',
      ]),
      api.post_process(DropExpectation),
  )
