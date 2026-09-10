# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from recipe_engine.post_process import StatusFailure, StatusSuccess, MustRun

DEPS = [
  'metadata_validator',
  'depot_tools/gerrit',
  'depot_tools/tryserver',
  'recipe_engine/buildbucket',
  'recipe_engine/json',
  'recipe_engine/properties',
]


def RunSteps(api):
  return api.metadata_validator.validate()


def GenTests(api):

  yield api.test(
    'basic',
    api.buildbucket.try_build(
      project='chromium',
      builder='linux-metadata-validator',
    ),
    api.metadata_validator.mock_change(
      {
        'third_party/foo/README.chromium': (
          'M',
          'Name: Foo\nLicense File: LICENSE\n',
        )
      }
    ),
    api.step_data(
      'Validate.Run validator',
      api.json.output({'files': {}, 'summary': {'invalid_files': 0}}),
    ),
    api.post_process(StatusSuccess),
  )
