# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to trigger toolchain packagers with the latest llvm revision.
"""

from recipe_engine import post_process

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/url',
]


def RunSteps(api):
  url = 'https://chromium.googlesource.com/external/github.com/llvm/llvm-project/+/refs/heads/main?format=JSON'
  rev = api.url.get_json(
      url,
      log=True,
      strip_prefix=api.url.GERRIT_JSON_PREFIX,
      default_test_data={
          'commit': 'abcd'
      }).output['commit']

  packge_builders = [
      'toolchain-pakager-linux',
      'toolchain-pakager-mac',
      'toolchain-pakager-mac-arm',
      'toolchain-pakager-windows',
  ]

  requests = [
      api.buildbucket.schedule_request(
          builder=b,
          bucket='official.toolchain',
          properties={
              'llvm_revision': rev,
          }) for b in packge_builders
  ]
  api.buildbucket.run(requests)


def GenTests(api):
  yield api.test(
      'full',
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
