# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to trigger toolchain packagers with the latest llvm revision.
"""

from recipe_engine import post_process

DEPS = [
    'recipe_engine/buildbucket',
    'recipe_engine/step',
    'recipe_engine/url',
]


def RunSteps(api):
  repo = 'https://chromium.googlesource.com/external/github.com/llvm/llvm-project'
  head_url = repo + '/+/refs/heads/main?format=JSON'
  rev = api.url.get_json(
      head_url,
      step_name='get the latest llvm revision',
      log=True,
      strip_prefix=api.url.GERRIT_JSON_PREFIX,
      default_test_data={
          'commit': 'abcd'
      }).output['commit']
  rev_url = '%s/+/%s' % (repo, rev)
  step = api.step.empty('revision ' + rev)
  step.presentation.links['Gitiles URL'] = rev_url

  packge_builders = [
      'toolchain-packager-linux',
      'toolchain-packager-mac',
      'toolchain-packager-mac-arm',
      'toolchain-packager-windows',
  ]

  requests = [
      api.buildbucket.schedule_request(
          builder=b,
          bucket='official.toolchain',
          can_outlive_parent=False,
          properties={
              'llvm_revision': rev,
          }) for b in packge_builders
  ]
  api.buildbucket.run(
      requests,
      timeout=5 * 60 * 60,  # timeout=5h
      raise_if_unsuccessful=True,
      url_title_fn=lambda b: b.builder.builder,
  )


def GenTests(api):
  yield api.test(
      'full',
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
