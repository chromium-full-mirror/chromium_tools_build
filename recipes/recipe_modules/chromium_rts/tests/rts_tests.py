# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests.steps import MockTestSpec

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_rts, chromium_tests
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    file,
    path,
    platform,
    properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_rts: chromium_rts.API
  chromium_tests: chromium_tests.API
  file: file.API
  path: path.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  file: file.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


def RunSteps(api: DEPS):
  experiments = api.buildbucket.build.input.experiments
  expected_generate = 'chromium_rts.filter_file_analysis' in experiments
  assert api.chromium_rts._should_generate_filters() == expected_generate

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
  mock_test = MockTestSpec.create(
      name='blink_web_tests',
      runs_on_swarming=True).get_test(api.chromium_tests)
  api.chromium_rts.start_evaluation(api.path.cleanup_dir, [mock_test])
  api.chromium_rts.wait_for_evaluation()

  command_line_variants = api.chromium_rts.get_command_line_variants(
      api.path.cleanup_dir, {
          'blink_web_tests': ['/bin/run_tests', '--some-arg'],
          'blink_python_tests': ['/bin/run_python_tests'],
      })
  if api.chromium_rts._should_generate_filters():
    expected_variants = {}
    if api.path.exists(
        api.path.cleanup_dir.joinpath('gen', 'rts', 'blink_web_tests.filter')):
      expected_variants['rts'] = {
          'blink_web_tests': [
              '/bin/run_tests', '--some-arg',
              '--test-launcher-filter-file=gen/rts/blink_web_tests.filter'
          ]
      }
    if api.path.exists(
        api.path.cleanup_dir.joinpath('gen', 'rts',
                                      'blink_web_tests_inverted.filter')):
      expected_variants['rts_complement'] = {
          'blink_web_tests': [
              '/bin/run_tests', '--some-arg',
              '--test-launcher-filter-file=gen/rts/blink_web_tests_inverted.filter'
          ]
      }
    assert command_line_variants == expected_variants
  else:
    assert command_line_variants == {}


def GenTests(api: TEST_DEPS):
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
          builder='linux-rel',
          experiments=['chromium_rts.filter_file_analysis']),
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
          builder='linux-rel',
          experiments=['chromium_rts.filter_file_analysis']),
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
