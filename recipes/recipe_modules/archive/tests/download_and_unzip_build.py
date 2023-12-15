# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, StepCommandContains

DEPS = [
    'archive',
    'recipe_engine/path',
    'recipe_engine/properties',
]


def RunSteps(api):
  api.path['checkout'] = api.path['cache'] / 'builder' / 'src'
  api.archive.download_and_unzip_build(
      'extract build',
      target=api.path['checkout'].join('Release', 'out'),
      build_url=api.archive.legacy_download_url('bucket_name'),
      build_archive_url=api.properties.get('build_archive_url'),
      build_revision='example_sha',
  )


def GenTests(api):
  yield api.test(
      'basic',
      api.properties(
          parent_buildername='example_buildername',
          parent_buildnumber=1.0,
          buildnumber=123,
      ),
      api.post_process(StepCommandContains, 'extract build', [
          '--build-url',
          'gs://bucket_name/example_buildername/full-build-linux.zip',
          '--build_revision',
          'example_sha',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'build_archive_url',
      api.properties(
          parent_buildername='example_buildername',
          parentname='example_buildername',
          buildnumber=123,
          build_archive_url='https://example/url'),
      api.post_process(StepCommandContains, 'extract build', [
          '--build-archive-url',
          'https://example/url',
          '--parent-builder-name',
          'example_buildername',
      ]),
      api.post_process(DropExpectation),
  )
