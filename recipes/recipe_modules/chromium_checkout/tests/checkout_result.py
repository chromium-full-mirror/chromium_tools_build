# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from RECIPE_MODULES.build.chromium_checkout import checkout_result

DEPS = [
    'chromium_checkout',
    'recipe_engine/path',
]


def RunSteps(api):
  checkout_dir = api.path.cache_dir / 'builder'
  source_dir = checkout_dir / 'src'

  # 1. Test CasCheckoutResult
  raw_properties = {
      'parent_got_revision': 'fake-parent-revision',
      'revision': 'fake-revision',
  }
  cas_result = checkout_result.CasCheckoutResult(checkout_dir, source_dir,
                                                 raw_properties, 'src')

  assert cas_result.checkout_dir == checkout_dir
  assert cas_result.source_root.path == source_dir
  assert cas_result.source_root.name == 'src'
  assert cas_result.patch_root is None
  assert cas_result.properties == {
      'got_revision': 'fake-parent-revision',
  }
  assert cas_result.manifest == {}
  assert cas_result.fixed_revisions == {}
  assert cas_result.out_commit is None

  # Test CasCheckoutResult with 'revision' fallback
  cas_result_fallback = checkout_result.CasCheckoutResult(
      checkout_dir, source_dir, {'revision': 'fake-revision'}, 'src')
  assert cas_result_fallback.properties == {
      'got_revision': 'fake-revision',
  }

  # 2. Test BotUpdateResultAdapter
  class MockSourceRoot:

    def __init__(self, path, name):
      self.path = path
      self.name = name

  class MockBotUpdateResult:

    def __init__(self):
      self.checkout_dir = checkout_dir
      self.source_root = MockSourceRoot(source_dir, 'src')
      self.patch_root = MockSourceRoot(source_dir / 'third_party/some_repo',
                                       'some_repo')
      self.properties = {'got_revision': 'fake-revision'}
      self.manifest = {'src': {'revision': 'fake-revision'}}
      self.fixed_revisions = {'src': 'HEAD'}
      self.out_commit = 'fake-out-commit'

  mock_real_result = MockBotUpdateResult()
  adapter = checkout_result.BotUpdateResultAdapter(mock_real_result)

  assert adapter.checkout_dir == checkout_dir
  assert adapter.source_root.path == source_dir
  assert adapter.source_root.name == 'src'
  assert adapter.patch_root.path == source_dir / 'third_party/some_repo'
  assert adapter.patch_root.name == 'some_repo'
  assert adapter.properties == {'got_revision': 'fake-revision'}
  assert adapter.manifest == {'src': {'revision': 'fake-revision'}}
  assert adapter.fixed_revisions == {'src': 'HEAD'}
  assert adapter.out_commit == 'fake-out-commit'

  # Test adapter with patch_root = None
  mock_real_result.patch_root = None
  adapter_no_patch = checkout_result.BotUpdateResultAdapter(mock_real_result)
  assert adapter_no_patch.patch_root is None


def GenTests(api):
  yield api.test(
      'basic',
      api.post_process(post_process.DropExpectation),
  )
