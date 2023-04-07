# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import (DropExpectation, LogContains,
                                        StepCommandContains, StepTextEquals)

DEPS = [
  'goma',
  'recipe_engine/properties',
]


def RunSteps(api):
  client_type = api.m.properties.get('client_type')
  additional_platforms = api.m.properties.get('additional_platforms')
  ephemeral = api.m.properties.get('ephemeral')
  api.goma.ensure_goma(client_type=client_type, additional_platforms=additional_platforms, ephemeral=ephemeral)
  if additional_platforms:
    api.goma.additional_goma_dir(additional_platforms[0])


def GenTests(api):

  def AssertCorrectClientType(client_type):
    total_characters = 16
    num_padding_characters = total_characters - len(client_type)
    return (
        api.post_process(StepCommandContains, 'ensure_goma.ensure_installed', [
            'infra_internal/goma/client/${platform} %s' % client_type,
        ]),
        api.post_process(LogContains, 'ensure_goma.ensure_installed',
                         'json.output', [
                             '"instance_id": "resolved-instance_id-of-%s%s"' %
                             (client_type, '-' * num_padding_characters),
                         ]),
    )

  yield api.test(
      'non_canary',
      api.properties(client_type='release'),
      *AssertCorrectClientType('release'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'canary',
      api.properties(client_type='candidate'),
      *AssertCorrectClientType('candidate'),
      api.post_process(StepTextEquals, 'ensure_goma',
                       'candidate goma client is selected'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'latest',
      api.properties(client_type='latest'),
      *AssertCorrectClientType('latest'),
      api.post_process(StepTextEquals, 'ensure_goma',
                       'latest goma client is selected'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'additional_canary',
      api.properties(client_type='candidate'),
      api.properties(additional_platforms=['chromeos-amd64']),
      *AssertCorrectClientType('candidate'),
      api.post_process(StepTextEquals, 'ensure_goma',
                       'candidate goma client is selected'),
      api.post_process(
          StepCommandContains, 'ensure_goma.ensure_installed (2)', [
              '-root',
              '[CACHE]/goma/extra/chromeos-amd64',
              '-ensure-file',
              'infra_internal/goma/client/chromeos-amd64 candidate',
          ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'ephemeral',
      api.properties(ephemeral=True),
      api.post_process(StepCommandContains, 'ensure_goma.ensure_installed', [
          '-root',
          '[TMP_BASE]/goma/client',
      ]),
      api.post_process(StepCommandContains, 'ensure_goma.ensure_installed (2)',
                       [
                           '-root',
                           '[TMP_BASE]/goma/bqupload',
                       ]),
      api.post_process(DropExpectation),
  )
