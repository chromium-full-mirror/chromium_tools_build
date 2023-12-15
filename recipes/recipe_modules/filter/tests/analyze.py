# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium

DEPS = [
    'chromium',
    'filter',
    'recipe_engine/assertions',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
]

def RunSteps(api):
  api.chromium.set_config('chromium')
  for c in api.properties.get('chromium_apply_config', []):
    api.chromium.apply_config(c)
  api.path['checkout'] = api.path['cache'] / 'builder' / 'src'

  # We convert these kwargs in analyze_kwargs from checkout-relative paths to
  # Path objects by joining them to api.path['checkout']
  kwargs = dict(api.properties.get('analyze_kwargs', {}))
  for k in ('mb_path', 'mb_config_path', 'build_output_dir'):
    if (val := kwargs.get(k)) is not None:
      kwargs[k] = api.path['checkout'].join(*val)

  affected_test_targets, affected_compile_targets = (
      api.filter.analyze(
          api.properties.get('affected_files', ['file1', 'file2']),
          api.properties.get('test_targets', ['test1', 'test2']),
          api.properties.get('compile_targets', ['compile1', 'compile2']),
          builder_id=chromium.BuilderId.create_for_group(
              'test_group', 'test_buildername'),
          **kwargs,
      ))

  expected_affected_test_targets = api.properties.get(
      'expected_affected_test_targets')
  if expected_affected_test_targets is not None:
    api.assertions.assertCountEqual(affected_test_targets,
                                    expected_affected_test_targets)
  expected_affected_compile_targets = api.properties.get(
      'expected_affected_compile_targets')
  if expected_affected_compile_targets is not None:
    api.assertions.assertCountEqual(affected_compile_targets,
                                    expected_affected_compile_targets)


def GenTests(api):
  yield api.test(
      'basic',
      api.platform('linux', 64),
      api.post_check(post_process.StepCommandContains, 'analyze',
                     [re.compile(r'.+/mb\.py')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'mac',
      api.platform('mac', 64),
      api.post_check(lambda check, steps: \
          check('FORCE_MAC_TOOLCHAIN' in steps['analyze'].env)),
      api.post_check(post_process.StepCommandContains, 'analyze',
                     [re.compile(r'.+/mb\.py')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'all-files-ignored',
      api.platform('linux', 64),
      api.override_step_data(
          'read filter exclusion spec',
          api.json.output({
              'base': {
                  'ignores': [r'.*\.cc'],
              },
          }),
      ),
      api.properties(
          affected_files=['foo.cc', 'bar.cc'],
          test_targets=['test1', 'test2'],
          compile_targets=['compile1', 'compile2'],
          expected_affected_test_targets=[],
          expected_affected_compile_targets=[],
      ),
      api.post_check(post_process.StepTextContains, 'analyze',
                     ['No compile necessary (all files ignored)']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no-dependency',
      api.filter.no_dependency(),
      api.properties(
          expected_affected_test_targets=[],
          expected_affected_compile_targets=[],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'found-dependency',
      api.platform('linux', 64),
      api.filter.analyze_output(
          status='Found dependency',
          test_targets=['test1'],
          compile_targets=['compile2'],
      ),
      api.properties(
          affected_files=['foo.cc', 'bar.cc'],
          test_targets=['test1', 'test2'],
          compile_targets=['compile1', 'compile2'],
          expected_affected_test_targets=['test1'],
          expected_affected_compile_targets=['test1', 'compile2'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'exclusion',
      api.platform('linux', 64),
      api.filter.exclude_everything(),
      api.properties(
          test_targets=['test1', 'test2'],
          compile_targets=['compile1', 'compile2'],
          expected_affected_test_targets=['test1', 'test2'],
          expected_affected_compile_targets=[
              'test1', 'test2', 'compile1', 'compile2'
          ],
      ),
      api.post_check(post_process.StepTextContains, 'analyze',
                     ['Analyze disabled: matched exclusion']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'additional_exclusions',
      api.platform('linux', 64),
      api.properties(
          affected_files=['generated/builder (dbg)/gn-args.json'],
          test_targets=['test1', 'test2'],
          compile_targets=['compile1', 'compile2'],
          expected_affected_test_targets=['test1', 'test2'],
          expected_affected_compile_targets=[
              'test1', 'test2', 'compile1', 'compile2'
          ],
          analyze_kwargs={
              'additional_exclusions': {
                  'generated/builder (dbg)/gn-args.json': 'test source'
              },
          }),
      api.post_check(post_process.StepTextContains, 'analyze',
                     ['Analyze disabled: matched exclusion']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'error',
      api.platform('linux', 64),
      api.override_step_data(
          'analyze',
          api.json.output({
              'error': 'fake-error',
          }),
      ),
      api.expect_status('FAILURE'),
      api.post_check(post_process.StepFailure, 'analyze'),
      api.post_check(post_process.StepTextContains, 'analyze', ['fake-error']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'invalid-targets',
      api.override_step_data(
          'analyze',
          api.json.output({
              'invalid_targets': ['test1', 'compile2'],
          }),
      ),
      api.expect_status('FAILURE'),
      api.post_check(post_process.ResultReasonRE,
                     'following targets were not found'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'non-mb',
      api.platform('linux', 64),
      api.properties(chromium_apply_config=['gn']),
      api.post_check(post_process.StepCommandContains, 'analyze',
                     [re.compile('.+/build/gyp_chromium')]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'custom-mb',
      api.platform('linux', 64),
      api.properties(
          analyze_kwargs={
              'mb_path': ['fake-mb-path'],
              'mb_config_path': ['fake-mb-config-path'],
              'build_output_dir': ['fake-build-output-dir'],
              'phase': 'fake-phase',
          }),
      api.post_check(post_process.StepCommandContains, 'analyze',
                     [re.compile(r'.+/fake-mb-path/mb\.py')]),
      api.post_check(post_process.StepCommandContains, 'analyze',
                     ['--config-file',
                      re.compile('.+/fake-mb-config-path')]),
      api.post_check(post_process.StepCommandContains, 'analyze',
                     [re.compile('.+/fake-build-output-dir')]),
      api.post_check(post_process.StepCommandContains, 'analyze',
                     ['--phase', 'fake-phase']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'non-posix-paths',
      api.platform('win', 64),
      api.properties(affected_files=[
          'path\\to\\changed\\file1', 'path\\to\\changed\\file2'
      ]),
      api.override_step_data(
          'read filter exclusion spec',
          api.json.output({
              'base': {
                  'ignores': ['path/to/changed/.*'],
              },
          }),
      ),
      api.post_check(post_process.StepTextContains, 'analyze',
                     ['No compile necessary (all files ignored)']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'empty-affected-file',
      api.platform('linux', 64),
      api.properties(affected_files=['path/to/changed/file1', '']),
      api.post_process(post_process.DropExpectation),
  )
