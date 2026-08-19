# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine import recipe_api
from recipe_engine import recipe_test_api
from RECIPE_MODULES.depot_tools.bot_update import api as bot_update_api

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'orderfile',
    'profiles',
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]

PROPERTIES = {
    'mock_orderfile': recipe_api.Property(default=True, kind=bool),
    'mock_pgo': recipe_api.Property(default=True, kind=bool),
    'arch': recipe_api.Property(default='arm', kind=str),
    'bitness': recipe_api.Property(default=32, kind=int),
}


def RunSteps(
    api: recipe_api.RecipeApi,
    mock_orderfile: bool,
    mock_pgo: bool,
    arch: str,
    bitness: int,
):
  checkout_dir = api.path.cache_dir / 'builder'
  source_dir = api.path.cache_dir / 'builder/src'

  _, builder_config = api.chromium_tests_builder_config.lookup_builder()

  api.chromium_tests.configure_build(builder_config)

  # Fake path.
  api.profiles.source_dir = api.path.start_dir

  api.chromium.set_config('chromium', TARGET_ARCH=arch, TARGET_BITS=bitness)

  api.orderfile.configure_custom_pgo_profile(source_dir)

  if mock_orderfile:
    api.path.mock_add_paths(
        api.profiles.profile_dir().joinpath('orderfile.out'))
  if mock_pgo:
    api.path.mock_add_paths(source_dir / 'chrome/build/pgo_profiles' /
                            'profile.pgo')

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

  api.orderfile.process_orderfile_data(source_dir, result)

  # coverage only
  _ = api.orderfile.using_orderfile

def GenTests(api: recipe_test_api.RecipeTestApi):

  yield api.test(
      'basic_android',
      api.chromium.ci_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(
          use_orderfile=True,
          upload_orderfile=True,
          last_uploaded_pgo_filename='profile.pgo',
      ),
      api.platform('linux', 32),
      api.post_process(
          post_process.MustRun,
          'processing generated orderfile.register '
          'chromium/chrome/android/orderfiles/arm',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_android_64',
      api.properties(bitness=64),
      api.chromium.ci_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(
          use_orderfile=True,
          upload_orderfile=True,
          last_uploaded_pgo_filename='profile.pgo',
      ),
      api.platform('linux', 64),
      api.post_process(
          post_process.MustRun,
          'processing generated orderfile.register'
          ' chromium/chrome/android/orderfiles/arm64',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_webview',
      api.chromium.ci_build(
          builder_group='chromium.perf',
          builder='android-go-wembley_webview-perf'),
      api.orderfile(
          use_orderfile=True,
          upload_orderfile=True,
          last_uploaded_pgo_filename='profile.pgo',
      ),
      api.platform('linux', 32),
      api.post_process(
          post_process.MustRun,
          'processing generated orderfile.register '
          'chromium/android_webview/tools/orderfiles/arm',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_webview_64',
      api.properties(bitness=64),
      api.chromium.ci_build(
          builder_group='chromium.perf',
          builder='android-go-wembley_webview-perf'),
      api.orderfile(
          use_orderfile=True,
          upload_orderfile=True,
          last_uploaded_pgo_filename='profile.pgo',
      ),
      api.platform('linux', 64),
      api.post_process(
          post_process.MustRun,
          'processing generated orderfile.register'
          ' chromium/android_webview/tools/orderfiles/arm64',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'missing_ref',
      api.chromium.generic_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(
          use_orderfile=True,
          upload_orderfile=True,
          last_uploaded_pgo_filename='profile.pgo',
      ),
      api.platform('linux', 32),
      api.post_process(post_process.SummaryMarkdownRE, 'Missing ref.*'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'unsupported_arch',
      api.properties(arch='intel'),
      api.chromium.ci_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(
          use_orderfile=True,
          upload_orderfile=True,
          last_uploaded_pgo_filename='profile.pgo',
      ),
      api.platform('linux', 32),
      api.post_process(post_process.SummaryMarkdownRE, 'Unsupported arch=.*'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'missing_orderfile',
      api.properties(mock_orderfile=False),
      api.chromium.ci_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(
          use_orderfile=True,
          upload_orderfile=True,
          last_uploaded_pgo_filename='profile.pgo',
      ),
      api.platform('linux', 32),
      api.post_process(post_process.SummaryMarkdownRE,
                       'Orderfile not found at.*'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'missing_pgo',
      api.properties(mock_pgo=False),
      api.chromium.ci_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(
          use_orderfile=True,
          upload_orderfile=True,
          last_uploaded_pgo_filename='profile.pgo',
      ),
      api.platform('linux', 32),
      api.post_process(
          post_process.StepTextContains,
          'processing generated orderfile',
          ('profile does not exist, skipping it for CIPD upload.',),
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_skip_android',
      api.chromium.ci_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(use_orderfile=True),
      api.platform('linux', 32),
      api.post_process(
          post_process.MustRunRE,
          'processing generated orderfile.skipping upload to CIPD.*',
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_skip_android_no_pgo_filename',
      api.chromium.ci_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(use_orderfile=True, upload_orderfile=True),
      api.platform('linux', 32),
      api.post_process(post_process.MustRunRE,
                       '.*skipping.*last_uploaded_pgo_filename.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'gs_bucket_no_file',
      api.chromium.ci_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(
          use_orderfile=True, gs_bucket='bucket', gs_bucket_path='path'),
      api.platform('linux', 32),
      api.post_process(post_process.MustRun, 'no custom PGO profile specified'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'gs_bucket_with_file',
      api.chromium.ci_build(
          builder_group='chromium.perf', builder='android-builder-perf'),
      api.orderfile(
          use_orderfile=True,
          gs_bucket='bucket',
          gs_bucket_path='path',
          last_uploaded_pgo_filename='profile.prof',
      ),
      api.platform('linux', 32),
      api.post_process(post_process.MustRun, 'override PGO profile'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'webview_pgo_orderfile',
      api.properties(bitness=64),
      api.chromium.ci_build(
          builder_group='chromium.perf',
          builder='android-go-wembley_webview-perf'),
      api.orderfile(
          use_orderfile=True,
          upload_orderfile=True,
          last_uploaded_pgo_filename='webview-profile.pgo',
      ),
      api.platform('linux', 64),
      api.post_process(
          post_process.MustRun,
          'processing generated orderfile.register'
          ' chromium/android_webview/tools/orderfiles/arm64',
      ),
      api.post_process(post_process.DropExpectation),
  )
