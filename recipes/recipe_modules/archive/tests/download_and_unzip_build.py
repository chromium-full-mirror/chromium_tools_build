# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DropExpectation, StepCommandContains

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import archive
from RECIPE_MODULES.recipe_engine import path, platform, properties


@dataclass
class DEPS(RecipeScriptApi):
  archive: archive.API
  path: path.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  source_dir = api.path.cache_dir / 'builder/src'
  api.archive.download_and_unzip_build(
    'extract build',
    target=source_dir / 'Release/out',
    build_url=api.archive.legacy_download_url('bucket_name'),
    build_archive_url=api.properties.get('build_archive_url'),
    build_revision='example_sha',
    source_dir=source_dir,
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.properties(
      parent_buildername='example_buildername',
      parent_buildnumber=1.0,
      buildnumber=123,
    ),
    api.post_process(
      StepCommandContains,
      'extract build',
      [
        '--build-url',
        'gs://bucket_name/example_buildername/full-build-linux.zip',
        '--build_revision',
        'example_sha',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'build_archive_url',
    api.properties(
      parent_buildername='example_buildername',
      buildnumber=123,
      build_archive_url='https://example/url',
    ),
    api.post_process(
      StepCommandContains,
      'extract build',
      [
        '--build-archive-url',
        'https://example/url',
      ],
    ),
    api.post_process(DropExpectation),
  )

  # Tests legacy download URL construction on Windows, ensuring
  # legacy_platform_name() resolves to 'win32'. (Previously covered as a side
  # effect of Windows ClusterFuzz archiving tests).
  yield api.test(
    'win',
    api.platform('win', 64),
    api.properties(
      parent_buildername='example_buildername',
      parent_buildnumber=1.0,
      buildnumber=123,
    ),
    api.post_process(
      StepCommandContains,
      'extract build',
      [
        '--build-url',
        'gs://bucket_name/example_buildername/full-build-win32.zip',
        '--build_revision',
        'example_sha',
      ],
    ),
    api.post_process(DropExpectation),
  )
