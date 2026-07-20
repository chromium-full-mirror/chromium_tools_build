# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEPS = [
    'chromium',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
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

  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = source_dir / 'out/Release'

  return api.chromium.compile(
      source_dir,
      build_dir,
      targets=api.properties.get('targets'),
      extra_ninja_args=api.properties.get('extra_ninja_args'),
      ninja_path=api.properties.get('ninja_path'))


def GenTests(api):

  yield api.test(
      'basic',
      api.chromium.generic_build(builder_group='test_group'),
      api.path.exists(
          api.path.cache_dir /
          'builder/src/tools/clang/scripts/process_crashreports.py'),
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
          api.path.cache_dir /
          'builder/src/tools/clang/scripts/process_crashreports.py'),
      api.post_process(post_process.TagEquals, 'compile',
                       'luci-bisection.is_bisectable', 'true'),
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
      api.platform('win', 64),
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
                       ['--llvm-force-head-revision']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rust_revision',
      api.properties(chromium_apply_config=['rust']),
      api.post_process(post_process.StepSuccess, 'rust_revision'),
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
          post_process.SummaryMarkdown,
          textwrap.dedent("""
          #### Step _compile_ failed. Error logs are shown below:
          ```
          /b/s/w/ir/cache/goma/client/gomacc ../../third_par...(too long)
          [1/1] CXX a.o
          filename:row:col: error: error info
          ```
          #### [Click here for more information](https:///logs///+/u/compile/raw_io.output_text_failure_summary_)
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
          post_process.SummaryMarkdown,
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
          #### [Click here for more information](https:///logs///+/u/compile/raw_io.output_text_failure_summary_)
          """).strip()),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'noop_failure',
      api.chromium.generic_build(builder_group='test_group'),
      api.override_step_data(
          'compile confirm no-op',
          stdout=api.raw_io.output_text('ninja explain: dirty')),
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.SummaryMarkdown,
          textwrap.dedent("""
          #### Step _compile_ failed. Error logs are shown below:
          ```
          Failing build because ninja reported work to do.
          This means that after completing a compile, another was run and
          it resulted in still having work to do (that is, a no-op build
          wasn't a no-op). Consult the first "ninja explain:" line for a
          likely culprit.
          ```
          #### [Click here for more information](https:///logs///+/u/compile_confirm_no-op/stderr)
          """).strip()),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'siso',
      api.properties(extra_ninja_args=['-j', '100'],),
      api.chromium.generic_build(builder_group='test_group'),
      api.siso.properties(),
      api.path.exists(
          api.path.cache_dir /
          'builder/src/tools/clang/scripts/process_crashreports.py'),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.post_process(post_process.StepSuccess, 'compile confirm no-op'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'siso_noop_failure',
      api.chromium.generic_build(builder_group='test_group'),
      api.siso.properties(),
      api.path.exists(
          api.path.cache_dir /
          'builder/src/tools/clang/scripts/process_crashreports.py'),
      api.post_process(post_process.StepSuccess, 'compile'),
      api.step_data(
          'compile confirm no-op',
          stdout=api.raw_io.output_text('ninja explain: dirty')),
      api.post_process(post_process.StepFailure, 'compile confirm no-op'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  build_dir = api.path.cache_dir / 'builder/src' / 'out/Release'

  yield api.test(
      'warning_failure',
      api.chromium.generic_build(builder_group='test_group'),
      api.chromium.properties(fail_build_on_clang_warnings=True),
      api.path.exists(build_dir / 'siso_output'),
      api.step_data(
          'check for compile warnings.read siso_output',
          api.file.read_text('build step: foo\n'
                             '../../a/b/c.cc:1:2: warning: '
                             'something bad happened [-Wwarning]\n'
                             '1 warning generated.\n'
                             'extra text after clang summary\n')),
      api.post_process(
          post_process.StepFailure,
          'check for compile warnings.scan siso_output for warnings'),
      api.post_process(
          post_process.LogDoesNotContain,
          'check for compile warnings.scan siso_output for warnings', 'stdout',
          ['extra text after clang summary']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'warning_failure_long_msg',
      api.chromium.generic_build(builder_group='test_group'),
      api.chromium.properties(fail_build_on_clang_warnings=True),
      api.path.exists(build_dir / 'siso_output'),
      api.step_data(
          'check for compile warnings.read siso_output',
          api.file.read_text('build step: foo\n'
                             '../../a/b/c.cc:1:2: warning: ' +
                             "long text " * 100 +
                             'something bad happened [-Wwarning]\n'
                             '2 warnings generated.')),
      api.post_process(
          post_process.StepFailure,
          'check for compile warnings.scan siso_output for warnings'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rust_warning_failure',
      api.chromium.generic_build(builder_group='test_group'),
      api.chromium.properties(fail_build_on_clang_warnings=True),
      api.path.exists(build_dir / 'siso_output'),
      api.step_data(
          'check for compile warnings.read siso_output',
          api.file.read_text(
              'stdout:\n'
              'warning: type `bar` should have an upper camel case name\n'
              '--> ../../mojo/public/rust/system/message.rs:37:8\n'
              '...\n'
              '= note: `#[warn(non_camel_case_types)]` '
              '(part of `#[warn(nonstandard_style)]`) on by default\n'
              '1 warning emitted\n'
              'extra text after rust summary\n')),
      api.post_process(
          post_process.StepFailure,
          'check for compile warnings.scan siso_output for warnings'),
      api.post_process(
          post_process.LogDoesNotContain,
          'check for compile warnings.scan siso_output for warnings', 'stdout',
          ['extra text after rust summary']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'lld_warning',
      api.chromium.generic_build(builder_group='test_group'),
      api.chromium.properties(fail_build_on_clang_warnings=True),
      api.path.exists(build_dir / 'siso_output'),
      api.step_data(
          'check for compile warnings.read siso_output',
          api.file.read_text(
              'build step: clang_arm64_link "./clang_arm64/protoc-gen-js"\n'
              'stderr:\n'
              'warning: no debug symbols in executable (-arch arm64)\n'
              'some extra unrelated log output after warning\n')),
      api.post_process(
          post_process.StepFailure,
          'check for compile warnings.scan siso_output for warnings'),
      api.post_process(
          post_process.LogContains,
          'check for compile warnings.scan siso_output for warnings', 'stdout',
          [
              'Sample warning text',
              'warning: no debug symbols in executable (-arch arm64)'
          ]),
      api.post_process(
          post_process.LogDoesNotContain,
          'check for compile warnings.scan siso_output for warnings', 'stdout',
          ['some extra unrelated log output after warning']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_warnings_no_failure',
      api.chromium.generic_build(builder_group='test_group'),
      api.chromium.properties(fail_build_on_clang_warnings=True),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'extra_ninja_args_and_path',
      api.chromium.generic_build(builder_group='test_group'),
      api.properties(
          extra_ninja_args=['-j', '100'],
          ninja_path='/some/ninja/path',
      ),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['-j', '100']),
      api.post_process(post_process.StepCommandContains, 'compile',
                       ['/some/ninja/path']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'siso infra failure',
      api.chromium.generic_build(builder_group='test_group'),
      api.siso.properties(),
      api.step_data('compile', retcode=1),
      api.path.exists(build_dir / 'siso_result.json'),
      api.step_data(
          'read siso_result.json',
          api.file.read_json({
              "infra_failure": True,
              "message": "too many fallback"
          })),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
