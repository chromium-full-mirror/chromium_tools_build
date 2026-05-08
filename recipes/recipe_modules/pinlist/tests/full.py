# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine import recipe_api
from recipe_engine import recipe_test_api
from RECIPE_MODULES.depot_tools.bot_update import api as bot_update_api

DEPS = [
    'chromium',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'pinlist',
    'depot_tools/bot_update',
    'recipe_engine/buildbucket',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
]

PROPERTIES = {
    'mock_pinlist': recipe_api.Property(default=True, kind=bool),
    'arch': recipe_api.Property(default='arm', kind=str),
    'bitness': recipe_api.Property(default=64, kind=int),
}


def RunSteps(
    api: recipe_api.RecipeApi,
    mock_pinlist: bool,
    arch: str,
    bitness: int,
):
  checkout_dir = api.path.cache_dir / 'builder'
  api.chromium_checkout.set_paths(checkout_dir, 'src')

  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  api.chromium_tests.configure_build(builder_config)

  api.chromium.set_config('chromium', TARGET_ARCH=arch, TARGET_BITS=bitness)

  if mock_pinlist:
    api.path.mock_add_paths(api.pinlist.pinlist_dir.joinpath('pinlist.meta'))

  result = bot_update_api.Result(
      checkout_dir=checkout_dir,
      source_root=bot_update_api.RelativeRoot.create(checkout_dir, 'src'),
      properties={},
      manifest={
          'src': bot_update_api.ManifestRepo(repository='src', revision='rev')
      },
      fixed_revisions={},
      out_commit=None,
  )

  api.pinlist.process_pinlist_data(result)
  api.pinlist.shard_merge()


def GenTests(api: recipe_test_api.RecipeTestApi):

  yield api.test(
      'basic_webview_64',
      api.chromium.ci_build(
          builder_group='chromium.perf',
          builder='android-go-wembley_webview-perf',
      ),
      api.pinlist(upload_pinlist=True),
      api.platform('linux', 64),
      api.post_process(
          post_process.MustRun,
          'processing generated pinlist.register'
          ' chromium/android_webview/tools/pinlist/arm64',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_skip_upload',
      api.chromium.ci_build(
          builder_group='chromium.perf',
          builder='android-go-wembley_webview-perf',
      ),
      api.pinlist(upload_pinlist=False),
      api.platform('linux', 64),
      api.post_process(
          post_process.MustRunRE,
          'processing generated pinlist.skipping upload to CIPD.*',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'unsupported_arch',
      api.properties(arch='arm', bitness=32),
      api.chromium.ci_build(
          builder_group='chromium.perf',
          builder='android-go-wembley_webview-perf',
      ),
      api.pinlist(upload_pinlist=True),
      api.platform('linux', 32),
      api.post_process(post_process.SummaryMarkdownRE, 'Unsupported arch=.*'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'unsupported_builder',
      api.chromium.ci_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.pinlist(upload_pinlist=True),
      api.platform('linux', 64),
      api.post_process(post_process.SummaryMarkdownRE,
                       'Unimplemented builder:.*'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'missing_pinlist',
      api.properties(mock_pinlist=False),
      api.chromium.ci_build(
          builder_group='chromium.perf',
          builder='android-go-wembley_webview-perf',
      ),
      api.pinlist(upload_pinlist=True),
      api.platform('linux', 64),
      api.post_process(post_process.SummaryMarkdownRE,
                       'Pinlist not found at.*'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'missing_ref',
      api.chromium.generic_build(
          builder_group='chromium.perf',
          builder='android-go-wembley_webview-perf',
      ),
      api.pinlist(upload_pinlist=True),
      api.platform('linux', 64),
      api.post_process(post_process.SummaryMarkdownRE, 'Missing ref.*'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
