# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'siso',
]

from recipe_engine.recipe_api import Property
from recipe_engine import post_process
import textwrap


def RunSteps(api):
  api.chromium.set_config(
      api.properties.get('chromium_config', 'chromium_clang'),
      TARGET_PLATFORM=api.properties.get('target_platform', 'linux'),
      TARGET_CROS_BOARDS=api.properties.get('target_cros_boards'))

  for config in api.properties.get('chromium_apply_config', []):
    api.chromium.apply_config(config)

  api.path.checkout_dir = api.path.cache_dir / 'builder' / 'src'

  return api.chromium.compile(targets=api.properties.get('targets'))


def GenTests(api):

  yield api.test(
      'basic',
      api.chromium.generic_build(builder_group='test_group'),
      api.path.exists(
          api.path.checkout_dir.join('tools', 'clang', 'scripts',
                                     'process_crashreports.py')),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'compile confirm no-op'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'targets_include_all',
      api.properties(targets=['foo', 'all', 'bar']),
      api.post_check(lambda check, steps: \
                     check('foo' not in steps['compile'].cmd)),
      api.post_check(lambda check, steps: \
                     check('all' not in steps['compile'].cmd)),
      api.post_check(lambda check, steps: \
                     check('bar' not in steps['compile'].cmd)),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'compile_fail',
      api.chromium.generic_build(builder_group='test_group'),
      api.step_data('compile', retcode=1),
      api.path.exists(
          api.path.checkout_dir.join('tools', 'clang', 'scripts',
                                     'process_crashreports.py')),
      api.post_process(post_process.MustRun, 'process clang crashes'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'infra_failure',
      api.chromium.generic_build(builder_group='test_group'),
      api.override_step_data('compile', retcode=2),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'codesearch',
      api.properties(chromium_apply_config=['codesearch']),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'compile confirm no-op'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'official_win_luci',
      api.properties(target_platform='win', chromium_apply_config=['official']),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'compile confirm no-op'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'chromeos',
      api.properties(
          target_platform='chromeos', target_cros_boards='x86-generic'),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'compile confirm no-op'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'chromeos_official',
      api.properties(
          target_platform='chromeos',
          target_cros_boards='x86-generic',
          chromium_apply_config=['official']),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'compile confirm no-op'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'android',
      api.properties(target_platform='android', chromium_config='android'),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'compile confirm no-op'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'clang_tot',
      api.properties(chromium_apply_config=['clang_tot']),
      api.post_process(post_process.StepCommandContains, 'clang_revision',
                       ['--use-tot-clang']),
      api.post_process(post_process.DropExpectation),
  )

  gomacc_path = ('/b/s/w/ir/cache/goma/client/gomacc '
        '../../third_party/llvm-build/Release+Asserts/bin/clang++ '
        'long string of commands\n'
  )

  yield api.test(
      'compile_failure_summary',
      api.chromium.generic_build(builder_group='test_group'),
      api.chromium.change_line_limit(50),
      api.override_step_data(
          'compile',
          api.raw_io.output_text(
              gomacc_path + textwrap.dedent("""
            [1/1] CXX a.o
            filename:row:col: error: error info
          """).strip(),
              name='failure_summary'),
          retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.ResultReason,
          textwrap.dedent("""
          #### Step _compile_ failed. Error logs are shown below:
          ```
          /b/s/w/ir/cache/goma/client/gomacc ../../third_par...(too long)
          [1/1] CXX a.o
          filename:row:col: error: error info
          ```
          #### More information in raw_io.output_text[failure_summary]
          """).strip()),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'long_compile_failure',
      api.chromium.generic_build(builder_group='test_group'),
      api.chromium.change_char_size_limit(350),
      api.chromium.change_line_limit(50),
      api.override_step_data(
          'compile',
          api.raw_io.output_text(
              gomacc_path + textwrap.dedent("""
          [1/1] CXX a.o
          filename error 1 info
          More stuff that happened in the error
          filename error 2 info
          Actual code of where the error happened
          filename error 3 info
          More stuff that happened in the error
          filename error 4 info
          filename error 5 info
          More stuff that happened in the error
          filename error 6 info
          """),
              name='failure_summary'),
          retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.ResultReason,
          textwrap.dedent("""
          #### Step _compile_ failed. Error logs are shown below:
          ```
          /b/s/w/ir/cache/goma/client/gomacc ../../third_par...(too long)

          [1/1] CXX a.o
          filename error 1 info
          More stuff that happened in the error
          filename error 2 info
          Actual code of where the error happened
          filename error 3 info
          More stuff that happened in the error
          filename error 4 info
          filename error 5 info
          More stuff that happened in the error
          ```
          ##### ...The message was too long...
          #### More information in raw_io.output_text[failure_summary]
          """).strip()),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'siso',
      api.chromium.generic_build(builder_group='test_group'),
      api.siso.properties(),
      api.path.exists(
          api.path.checkout_dir.join('tools', 'clang', 'scripts',
                                     'process_crashreports.py')),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'compile confirm no-op'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'siso_noop_failure',
      api.chromium.generic_build(builder_group='test_group'),
      api.siso.properties(),
      api.path.exists(
          api.path.checkout_dir.join('tools', 'clang', 'scripts',
                                     'process_crashreports.py')),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.step_data(
          'compile confirm no-op',
          stdout=api.raw_io.output_text('ninja explain: dirty')),
      api.post_process(post_process.StepFailure, 'compile confirm no-op'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
