# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import base64
from recipe_engine import post_process

from PB.recipe_modules.build.symupload import properties

DEPS = [
    'chromium',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'symupload',
]


def RunSteps(api):
  api.chromium.set_config(
      'chromium', **{
          'TARGET_PLATFORM': api.properties.get('target_platform'),
          'HOST_PLATFORM': api.properties.get('host_platform')
      })

  api.symupload(
      api.path.tmp_base_dir,
      config_file_path=api.path.cache_dir.joinpath('path', 'to', 'config.json'))


def GenTests(api):

  yield api.test(
      'symupload_file',
      api.properties(target_platform='mac', host_platform='mac'),
      api.platform('mac', 64),
      api.path.exists(api.path.tmp_base_dir / 'symupload',
                      api.path.cache_dir.joinpath('path', 'to', 'config.json')),
      api.post_process(
          post_process.StepCommandContains, 'symupload.symupload_v2', [
              "--artifacts", "[TMP_BASE]/some_artifact.txt", "--api-key-file",
              "[CLEANUP]/symupload-api-key.txt", "--binary-path",
              "[TMP_BASE]/symupload", "--platform", "mac", "--server-urls",
              "https://some.url.com"
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'non_existing_source_side_spec_path',
      api.properties(target_platform='mac', host_platform='mac'),
      api.platform('mac', 64),
      api.path.exists(api.path.tmp_base_dir.joinpath('symupload')),
      api.post_process(post_process.StepException,
                       'Could not find specified symupload config'),
      api.post_process(post_process.DropExpectation),
      status='INFRA_FAILURE',
  )
