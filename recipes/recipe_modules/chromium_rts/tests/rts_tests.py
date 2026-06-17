# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium',
    'chromium_rts',
    'recipe_engine/buildbucket',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
]


def RunSteps(api):
  expected_shadow_mode = ('chromium_rts.filter_file_analysis'
                          in api.buildbucket.build.input.experiments)
  assert api.chromium_rts._should_generate_filters() == expected_shadow_mode

  api.chromium_rts.generate_filter_files(api.path.start_dir,
                                         api.path.cleanup_dir)


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium.try_build(builder='linux-rel'),
      api.post_process(post_process.DoesNotRun,
                       'generate chromium-rts filter files'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_enabled',
      api.chromium.try_build(
          builder='linux-rel',
          experiments=['chromium_rts.filter_file_analysis']),
      api.post_process(post_process.MustRun,
                       'generate chromium-rts filter files'),
      api.post_process(
          post_process.StepCommandContains,
          'generate chromium-rts filter files',
          [
              '[CLEANUP]/rts-model/rts-chromium',
              'select',
              '-checkout',
              '[START_DIR]',
              '-model-dir',
              '[CLEANUP]/rts-model',
              '-out',
              '[CLEANUP]/gen/rts',
          ],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_enabled_win',
      api.platform('win', 64),
      api.chromium.try_build(
          builder='win-rel', experiments=['chromium_rts.filter_file_analysis']),
      api.post_process(post_process.MustRun,
                       'generate chromium-rts filter files'),
      api.post_process(
          post_process.StepCommandContains,
          'generate chromium-rts filter files',
          [
              '[CLEANUP]\\rts-model\\rts-chromium.exe',
              'select',
              '-checkout',
              '[START_DIR]',
              '-model-dir',
              '[CLEANUP]\\rts-model',
              '-out',
              '[CLEANUP]\\gen\\rts',
          ],
      ),
      api.post_process(post_process.DropExpectation),
  )
