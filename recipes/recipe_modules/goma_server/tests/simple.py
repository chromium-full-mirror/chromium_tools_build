# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DropExpectation, StepCommandContains

DEPS = [
  'recipe_engine/buildbucket',
  'goma_server',
]


def RunSteps(api):
  api.goma_server.BuildAndTest(
      'git://goma-server/',
      'goma-server',
      allow_diff=False)


def GenTests(api):
  yield api.test(
      'simple',
      api.buildbucket.try_build(
          builder='Goma Server Trusty Presubmit',
          change_number=4840,
          patch_set=2),
      api.post_process(StepCommandContains, 'git setup', [
          '--url',
          'git://goma-server/',
      ]),
      api.post_process(StepCommandContains, 'generate proto', [
          'generate',
          'goma-server/proto/...',
      ]),
      api.post_process(StepCommandContains, 'build', [
          'install',
          'goma-server/cmd/...',
      ]),
      api.post_process(StepCommandContains, 'test', [
          '-cover',
          'goma-server/...',
      ]),
      api.post_process(StepCommandContains, 'go vet', [
          'vet',
          'goma-server/...',
      ]),
      api.post_process(StepCommandContains, 'go fmt', [
          'fmt',
          'goma-server/...',
      ]),
      api.post_process(DropExpectation),
  )
