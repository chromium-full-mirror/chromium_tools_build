# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

DEPS = [
    'chromium',
    'chromium_rts',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
]


def RunSteps(api):
  experiments = api.buildbucket.build.input.experiments
  expected_generate = ('chromium_rts.filter_file_analysis' in experiments or
                       'chromium_rts.skip_tests' in experiments)
  assert api.chromium_rts._should_generate_filters() == expected_generate

  expected_skip = 'chromium_rts.skip_tests' in experiments
  assert api.chromium_rts._should_skip_tests() == expected_skip

  api.chromium_rts.get_experiment_names()

  api.chromium_rts.generate_filter_files(
      api.path.start_dir,
      api.path.cleanup_dir,
      affected_files=api.properties.get('affected_files', []),
  )
  api.chromium_rts.isolate_filter_files(
      api.path.cleanup_dir,
      ['blink_python_tests', 'blink_web_tests'],
  )
  api.chromium_rts.start_evaluation(api.path.cleanup_dir, [])
  api.chromium_rts.wait_for_evaluation()

  command_line_variants = api.chromium_rts.get_command_line_variants(
      api.path.cleanup_dir, {
          'blink_web_tests': ['/bin/run_tests', '--some-arg'],
          'blink_python_tests': ['/bin/run_python_tests'],
      })
  if api.chromium_rts._should_skip_tests():
    assert command_line_variants == {
        'rts': {
            'blink_web_tests': [
                '/bin/run_tests', '--some-arg',
                '--test-launcher-filter-file=gen/rts/blink_web_tests.filter'
            ]
        },
        'rts_complement': {
            'blink_web_tests': [
                '/bin/run_tests', '--some-arg',
                '--test-launcher-filter-file=gen/rts/blink_web_tests_inverted.filter'
            ]
        }
    }
  else:
    assert command_line_variants == {}


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

  yield api.test(
      'rts_enabled_with_banned_suites',
      api.chromium.try_build(
          builder='linux-rel',
          experiments=['chromium_rts.filter_file_analysis']),
      api.path.exists(
          api.path.cleanup_dir.joinpath('gen', 'rts',
                                        'blink_python_tests.filter'),
          api.path.cleanup_dir.joinpath('gen', 'rts', 'blink_web_tests.filter'),
      ),
      api.post_process(post_process.MustRun,
                       'generate chromium-rts filter files'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_skip_tests_enabled',
      api.chromium.try_build(
          builder='linux-rel', experiments=['chromium_rts.skip_tests']),
      api.path.exists(
          api.path.cleanup_dir.joinpath('gen', 'rts', 'blink_web_tests.filter'),
          api.path.cleanup_dir.joinpath('gen', 'rts',
                                        'blink_web_tests_inverted.filter'),
          api.path.cleanup_dir.joinpath('gen', 'rts',
                                        'blink_python_tests.filter'),
          api.path.cleanup_dir.joinpath('gen', 'rts',
                                        'blink_python_tests_inverted.filter'),
          api.path.cleanup_dir.joinpath('blink_web_tests.isolate'),
      ),
      api.override_step_data(
          'add RTS filter files to isolates.Read '
          '[CLEANUP]/blink_web_tests.isolate',
          api.file.read_json({'variables': {
              'files': []
          }}),
      ),
      api.post_process(post_process.MustRun,
                       'generate chromium-rts filter files'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_skip_tests_missing_isolate',
      api.chromium.try_build(
          builder='linux-rel', experiments=['chromium_rts.skip_tests']),
      api.path.exists(
          api.path.cleanup_dir.joinpath('gen', 'rts', 'blink_web_tests.filter'),
          api.path.cleanup_dir.joinpath('gen', 'rts',
                                        'blink_web_tests_inverted.filter'),
      ),
      api.post_process(
          post_process.MustRun,
          'add RTS filter files to isolates.missing isolate files'),
      api.post_process(
          post_process.StepSuccess,
          'add RTS filter files to isolates.missing isolate files'),
      api.post_process(
          post_process.StepTextContains,
          'add RTS filter files to isolates.missing isolate files',
          ['blink_web_tests'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_enabled_with_affected_files',
      api.properties(affected_files=['foo/bar.cc', 'baz.py']),
      api.chromium.try_build(
          builder='linux-rel',
          experiments=['chromium_rts.filter_file_analysis']),
      api.post_process(post_process.MustRun,
                       'generate chromium-rts filter files'),
      api.post_process(
          post_process.StepCommandContains,
          'generate chromium-rts filter files',
          [
              '-changed-files-path',
          ],
      ),
      api.post_process(post_process.DropExpectation),
  )
