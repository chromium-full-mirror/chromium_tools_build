# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import textwrap

from recipe_engine import post_process

from PB.recipe_modules.build.chromium_utr.request import Request
from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
  chromium_utr,
  code_coverage,
  siso,
  test_utils,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  file,
  json,
  path,
  platform,
  properties,
  raw_io,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  chromium_utr: chromium_utr.API
  code_coverage: code_coverage.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  siso: siso.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_swarming: chromium_swarming.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  code_coverage: code_coverage.TEST_API
  file: file.TEST_API
  json: json.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  siso: siso.TEST_API
  test_utils: test_utils.TEST_API


PROPERTIES = Request


def RunSteps(api: DEPS, request):
  source_dir = api.path.cast_to_path(request.checkout_path)
  checkout_dir = source_dir.parent
  api.chromium_checkout.set_paths(checkout_dir, source_dir)

  builder = api.buildbucket.build.builder.builder
  builder_id = chromium_types.BuilderId.create_for_group(
    api.m.properties['builder_group'], builder
  )

  api.chromium.verify_config = not request.rerun_options.skip_config_validation

  _, builder_config = api.chromium_tests_builder_config.lookup_builder(
    use_try_db=True
  )
  api.chromium_tests.configure_build(
    builder_config, test_only=request.run_type == Request.RunType.RUN_TYPE_RUN
  )

  return api.chromium_utr.run(
    request, checkout_dir, source_dir, builder_id, builder_config
  )


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  def gen_ctbc_properties(builder_spec=None):
    return ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
        builder_spec=builder_spec,
      )
      .with_mirrored_tester(
        builder_group='fake-group',
        builder='fake-tester',
      )
      .assemble()
    )

  def boilerplate_properties(
    run_type=Request.RunType.RUN_TYPE_COMPILE_AND_RUN,
    test_names=None,
    checkout_path='[CACHE]/src',
    build_dir='[CACHE]/src/out/Release',
    builder_recipe='chromium',
    output_properties_file='checkout/output_properties.json',
    # Re-run options.
    preserve_gn_args=False,
    bypass_gclient=True,
    bypass_gn_args=True,
    bypass_branch_check=False,
    skip_instrumentation=False,
    **kwargs,
  ):
    if not test_names:
      test_names = ['browser_tests']
    return api.properties(
      build_dir=build_dir,
      run_type=run_type,
      test_names=test_names,
      checkout_path=checkout_path,
      output_properties_file=output_properties_file,
      builder_recipe=builder_recipe,
      rerun_options=Request.RerunOptions(
        bypass_gclient=bypass_gclient,
        bypass_gn_args=bypass_gn_args,
        preserve_gn_args=preserve_gn_args,
        bypass_branch_check=bypass_branch_check,
        skip_instrumentation=skip_instrumentation,
      ),
      **kwargs,
    )

  def boilerplate(build=None, ctbc_properties=None, target_spec=None, **kwargs):
    test_data = [
      boilerplate_properties(**kwargs),
      ctbc_properties
      if ctbc_properties
      else gen_ctbc_properties(
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
        ),
      ),
      build
      or api.chromium.generic_build(
        builder_group='fake-group',
        builder='fake-tester',
      ),
    ]
    # Overload 'None' and 'False' for this arg to indicate default test data vs
    # no test data.
    if target_spec is None:
      target_spec = {
        'fake-tester': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
              },
            }
          ],
        },
      }
    if target_spec:
      target_spec_data = api.chromium_tests.read_targets_spec(
        'fake-group', target_spec
      )
      test_data.append(target_spec_data)
    return sum(test_data, api.empty_test_data())

  yield api.test(
    'basic',
    boilerplate(
      preserve_gn_args=False,
      bypass_gclient=False,
      target_spec={
        'fake-tester': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
              },
            },
            {
              'name': 'not_run_test',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
              },
            },
          ],
        }
      },
    ),
    api.platform('linux', 64),
    api.path.exists(api.path.cache_dir / '.gclient'),
    api.step_data(
      'read gclient',
      api.file.read_text("""
solutions = [
  {
    'url': 'https://chromium.googlesource.com/chromium/src.git',
    'custom_vars': {
      'checkout_telemetry_dependencies': False,
    },
  },
]
"""),
    ),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(post_process.MustRun, 'isolate tests'),
    api.post_process(post_process.MustRun, 'lookup GN args'),
    api.post_process(
      post_process.MustRun, 'test_pre_run.[trigger] browser_tests'
    ),
    api.post_process(
      post_process.StepCommandContains,
      'compile',
      ['[CACHE]/src/out/Release', 'browser_tests'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'isolate tests',
      ['[CACHE]/src/out/Release/browser_tests.isolated.gen.json'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'generate_build_files',
      ['-m', 'fake-group', '-b', 'fake-tester'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'find command lines',
      ['[CACHE]/src/out/Release'],
    ),
    api.post_process(post_process.MustRun, 'browser_tests'),
    api.post_process(post_process.DoesNotRun, 'upload_ninja_log'),
    api.post_process(
      post_process.StepCommandDoesNotContain, 'compile', ['not_run_test']
    ),
    api.post_process(
      post_process.StepCommandDoesNotContain,
      'isolate tests',
      ['fake_root/fake_out/Debug/not_run_test.isolated.gen.json'],
    ),
    api.post_process(post_process.DoesNotRun, 'not_run_test'),
    api.post_process(post_process.DropExpectation),
  )

  def _check_dimension(check, step_odict, step, key, value):
    # Check if the trigger step uses (or in the case of a None
    # value, does not use) the provided dimension
    trigger_step = step_odict[step]
    json_flag_index = trigger_step.cmd.index('-json-input')
    check(json_flag_index < len(trigger_step.cmd))
    json_input = api.json.loads(trigger_step.cmd[json_flag_index + 1])
    dims = json_input['requests'][0]['task_slices'][0]['properties'][
      'dimensions'
    ]
    entry = {'key': key, 'value': value}
    if value == None:
      check(entry not in dims)
    else:
      check(entry in dims)

  yield api.test(
    'swarming_overrides',
    boilerplate(
      target_spec={
        'fake-tester': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'changed_dim': 'foo',
                  'removed_dim': 'bar',
                  'unchanged_dim': 'baz',
                },
              },
            }
          ],
        }
      },
      swarming_dimensions=[
        'changed_dim=new-value',
        'removed_dim=',
        'equal_value=foo=bar',
      ],
      swarming_shards=10,
    ),
    api.platform('linux', 64),
    api.post_process(
      _check_dimension,
      'test_pre_run.[trigger] browser_tests',
      'changed_dim',
      'new-value',
    ),
    api.post_process(
      _check_dimension,
      'test_pre_run.[trigger] browser_tests',
      'unchanged_dim',
      'baz',
    ),
    api.post_process(
      _check_dimension,
      'test_pre_run.[trigger] browser_tests',
      'removed_dim',
      None,
    ),
    api.post_process(
      _check_dimension,
      'test_pre_run.[trigger] browser_tests',
      'equal_value',
      'foo=bar',
    ),
    api.post_process(
      post_process.MustRun, 'test_pre_run.[trigger] browser_tests (10)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'skylab',
    boilerplate(
      ctbc_properties=gen_ctbc_properties(
        builder_spec=ctbc.BuilderSpec.create(
          chromium_config='chromium',
          gclient_config='chromium',
          skylab_gs_bucket='gs://bar-bucket',
          skylab_gs_extra='lacros',
        ),
      ),
      target_spec={
        'fake-tester': {
          'skylab_tests': [
            {
              'name': 'skylab_test',
              'cros_board': 'foo-board',
              'autotest_name': 'chromium',
            }
          ]
        }
      },
      test_names=['skylab_test'],
    ),
    api.path.exists(
      api.path.cache_dir.joinpath(
        'src', 'out', 'Release', 'skylab_test.isolate'
      )
    ),
    api.step_data(
      'prepare skylab tests.'
      'collect runtime deps for skylab_test.read isolate file',
      api.file.read_text("""
          {'variables':
            {'command': ['foo/skylab_test', '--logs-dir=${ISOLATED_OUTDIR}'],
                'files': ['../../.vpython',
                          'bin/skylab_test',
                          './chrome'
            ]}
          }"""),
    ),
    api.override_step_data(
      'skylab_test results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results('skylab_test', passing_tests=['Test.Two'])
      ),
    ),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(post_process.MustRun, 'lookup GN args'),
    api.post_process(
      post_process.StepCommandContains,
      'compile',
      ['[CACHE]/src/out/Release', 'skylab_test'],
    ),
    api.post_process(post_process.DoesNotRun, 'upload_ninja_log'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'skylab_with_extra_args',
    boilerplate(
      ctbc_properties=gen_ctbc_properties(
        builder_spec=ctbc.BuilderSpec.create(
          chromium_config='chromium',
          gclient_config='chromium',
          skylab_gs_bucket='gs://bar-bucket',
          skylab_gs_extra='lacros',
        ),
      ),
      additional_test_args=['foo=bar'],
      target_spec={
        'fake-tester': {
          'skylab_tests': [
            {
              'name': 'skylab_test',
              'cros_board': 'foo-board',
              'autotest_name': 'chromium',
            }
          ]
        }
      },
      test_names=['skylab_test'],
    ),
    api.post_process(
      post_process.SummaryMarkdown,
      'Modifying test arguments is not supported on skylab tests',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'invalid_swarming_task',
    boilerplate(),
    api.override_step_data(
      'browser_tests',
      api.chromium_swarming.canned_summary_output(
        api.test_utils.gtest_results('invalid results', 1), failure=True
      ),
    ),
    api.post_process(
      post_process.MustRun, 'test_pre_run.[trigger] browser_tests'
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'build_path_not_set_in_recipe_engine',
    boilerplate(
      build_dir='/foo/bar/path', run_type=Request.RunType.RUN_TYPE_COMPILE
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'default_build_path',
    boilerplate(build_dir='', run_type=Request.RunType.RUN_TYPE_COMPILE),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'custom_ninja_path',
    boilerplate(
      run_type=Request.RunType.RUN_TYPE_COMPILE,
      autoninja_path='/some/autoninja/path',
    ),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'skip_test',
    boilerplate(run_type=Request.RunType.RUN_TYPE_COMPILE),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(post_process.MustRun, 'isolate tests'),
    api.post_process(
      post_process.StepCommandContains,
      'generate_build_files',
      ['-m', 'fake-group', '-b', 'fake-tester'],
    ),
    api.post_process(
      post_process.DoesNotRun, 'test_pre_run.[trigger] browser_tests'
    ),
    api.post_process(post_process.DoesNotRun, 'browser_tests'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'failed_build',
    boilerplate(),
    api.override_step_data('compile', retcode=1),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(
      post_process.DoesNotRun, 'test_pre_run.[trigger] browser_tests'
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'reuse_swarming_task',
    boilerplate(reuse_swarming_task='12341234'),
    api.step_data(
      'get_reuse_swarming_task',
      stdout=api.json.output(
        {
          'tags': ['test_suite:browser_tests'],
          'task_slices': [
            {
              'properties': {
                'command': ['rdb', 'stream', '--', 'run_test'],
                'relative_cwd': 'some/dir',
                'cas_input_root': {
                  'cas_instance': 'projects/example-project/instances/default_instance',
                  'digest': {
                    'hash': '24b2420bc49d8b8fdc1d011a163708927532b37dc9f91d7d8d6877e3a86559ca',
                    'size_bytes': '73',
                  },
                },
                'dimensions': [
                  {
                    'key': 'pool',
                    'value': 'swarming-pool',
                  }
                ],
              }
            }
          ],
        }
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'reuse_swarming_task_wrong_test',
    boilerplate(reuse_swarming_task='12341234'),
    api.step_data(
      'get_reuse_swarming_task',
      stdout=api.json.output(
        {
          'tags': ['test_suite:unit_tests'],
        }
      ),
    ),
    api.post_process(
      post_process.SummaryMarkdown,
      'The provided swarming task does not appear to match the requested '
      'test. Requested browser_tests but trying to reuse unit_tests',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'reuse_non_isolated',
    boilerplate(
      reuse_swarming_task='12341234',
      target_spec={
        'fake-tester': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
            }
          ],
        }
      },
    ),
    api.post_process(
      post_process.SummaryMarkdown,
      'Only one test that uses swarming can be reused at a time',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'child_tester',
    boilerplate_properties(),
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-tester',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            'fake-tester': ctbc.BuilderSpec.create(
              execution_mode=ctbc.TEST,
              parent_buildername='fake-builder',
              parent_builder_group='fake-group',
              chromium_config='chromium',
              gclient_config='chromium',
            ),
          },
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-tester': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
              },
            }
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'browser_tests'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'unsupported_child_testers',
    boilerplate_properties(),
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-grandchild-tester',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            'fake-child-tester': ctbc.BuilderSpec.create(
              execution_mode=ctbc.TEST,
              parent_buildername='fake-builder',
              parent_builder_group='fake-group',
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            'fake-grandchild-tester': ctbc.BuilderSpec.create(
              execution_mode=ctbc.TEST,
              parent_buildername='fake-child-tester',
              parent_builder_group='fake-group',
              chromium_config='chromium',
              gclient_config='chromium',
            ),
          },
        }
      ),
    ),
    api.post_process(
      post_process.SummaryMarkdownRE,
      'Unsupported UTR invocation for builder fake-group:fake-grandchild-tester.*',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_tests',
    boilerplate(test_names=['non_existant_test']),
    api.post_process(
      post_process.SummaryMarkdownRE,
      'No suites on the bot matched the request for non_existant_test',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'missing_gclient',
    boilerplate(bypass_gclient=False, target_spec=False),
    api.expect_exception('FileNotFoundError'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'src_not_in_gclient',
    boilerplate(bypass_gclient=False, target_spec=False),
    api.path.exists(api.path.cache_dir.joinpath('.gclient')),
    api.step_data(
      'read gclient',
      api.file.read_text("""
solutions = [
  {
    'custom_vars': {
      'rbe_instance': 'fake_instance',
    },
  },
]
target_os=['os']
"""),
    ),
    api.post_process(
      post_process.SummaryMarkdown,
      'Caution: your .gclient file could not be validated. Exactly one '
      'solution with \'url\' set to the URL for the repo the UTR is '
      'being used with must be set\n',
    ),
    api.post_process(
      post_process.StepCommandContains, 'read gclient', ['[CACHE]/.gclient']
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'gclient_in_src',
    boilerplate(bypass_gclient=False, target_spec=False),
    api.path.exists(api.path.cache_dir.joinpath('src', '.gclient')),
    api.step_data(
      'read gclient',
      api.file.read_text(
        textwrap.dedent("""\
              solutions = [
                {
                  'custom_vars': {
                    'rbe_instance': 'fake_instance',
                  },
                },
              ]
              target_os=['os']
              """)
      ),
    ),
    api.post_process(
      post_process.SummaryMarkdown,
      'Caution: your .gclient file could not be validated. Exactly one '
      'solution with \'url\' set to the URL for the repo the UTR is '
      'being used with must be set\n',
    ),
    api.post_process(
      post_process.StepCommandContains, 'read gclient', ['[CACHE]/src/.gclient']
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'bad_gclient',
    boilerplate(
      preserve_gn_args=False,
      bypass_gclient=False,
      target_spec=False,
      ctbc_properties=gen_ctbc_properties(
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='ios',
          gclient_apply_config=['chromium_with_telemetry_dependencies'],
          chromium_config='chromium',
        ),
      ),
    ),
    api.path.exists(api.path.cache_dir.joinpath('.gclient')),
    api.step_data(
      'read gclient',
      api.file.read_text("""
solutions = [
  {
    'url': 'https://chromium.googlesource.com/chromium/src.git',
    'custom_vars': {
      'rbe_instance': 'fake_instance',
    },
  },
]
target_os=['os']
"""),
    ),
    api.post_process(
      post_process.SummaryMarkdown,
      'Caution: your .gclient file and the builder\'s mismatches in the '
      'following way(s). Please run "gclient sync" after resolving these:\n'
      '- rbe_instance has been set in the .gclient file\n'
      '- custom_var checkout_telemetry_dependencies has mismatched value '
      'in the local .gclient file. Set it to: '
      '`"checkout_telemetry_dependencies": "True"`\n'
      '- target_os in builder config `"[\'ios\']"` is not in the local '
      '.gclient file. Set it to: `target_os = ["os", "ios"]`',
    ),
    api.post_process(
      post_process.StepCommandContains, 'read gclient', ['[CACHE]/.gclient']
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'bad_gn_args',
    boilerplate(bypass_gn_args=False, target_spec=False, no_siso=True),
    api.path.exists(api.path.cache_dir / 'src/out/Release/args.gn'),
    api.step_data(
      'lookup_builder_gn_args',
      stdout=api.raw_io.output_text(
        'import("//builder.args")\nuse_siso = true\na = "1"\nc = true\nd = true'
      ),
    ),
    api.step_data(
      'read GN args',
      api.raw_io.output_text(
        'import("//local.args")\nuse_siso=false\nb="2"\nc=false\nd=true'
      ),
    ),
    # Note that there should be no "use_siso" warning present in the summary
    # since the user passed in "--no-siso".
    api.post_process(
      post_process.SummaryMarkdown,
      'Caution: your build\'s gn args set in '
      '[CACHE]/src/out/Release/args.gn and the builder\'s mismatches in '
      'the following way(s):\n'
      '- `b` in current build dir is not set by the builder\n'
      '- `c` in current build (`false`) does not match builder value '
      '(`true`)\n'
      '- `a` set by the builder is absent in the current build dir\n'
      '- `import("//builder.args")` used by the builder is absent in the '
      'current build dir\n'
      '- `import("//local.args")` in current build dir is not used by the '
      'builder',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'code_coverage',
    boilerplate(
      checkout_path='[CACHE]\\src',
      build_dir='[CACHE]\\src\\out\\Release',
      builder_recipe='chromium_trybot',
      build=api.chromium.generic_build(
        builder_group='fake-group',
        builder='fake-tester',
        bucket='try',
      ),
    ),
    api.platform('win', 32),
    api.code_coverage(use_clang_coverage=True),
    api.post_process(post_process.MustRun, 'save paths of affected files'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'code_coverage_no_upstream',
    boilerplate(builder_recipe='chromium_trybot', target_spec=False),
    api.code_coverage(use_clang_coverage=True),
    api.step_data(
      'check upstream branch',
      retcode=1,
    ),
    api.post_process(
      post_process.SummaryMarkdown,
      'Caution: failed to get an upstream branch from the current checkout',
    ),
    api.post_process(
      post_process.MustRun,
      'write output_properties_file checkout/output_properties.json',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'code_coverage_skip_instrument',
    boilerplate(
      build_dir='[CACHE]/src/out/Release',
      builder_recipe='chromium_trybot',
      bypass_branch_check=True,
      skip_instrumentation=True,
    ),
    api.code_coverage(use_clang_coverage=True),
    api.post_process(post_process.MustRun, 'save paths of affected files'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'code_coverage_instrument_everything_user_gn_args_win',
    boilerplate(
      build_dir='[CACHE]/src/out/Release',
      builder_recipe='chromium_trybot',
      bypass_branch_check=True,
      skip_instrumentation=False,
      preserve_gn_args=True,
    ),
    api.code_coverage(use_clang_coverage=True),
    api.path.exists(
      api.path.cache_dir.joinpath('src', 'out', 'Release', 'args.gn')
    ),
    api.step_data(
      'read GN args',
      api.raw_io.output_text(
        'coverage_instrumentation_input_file = '
        '".code-coverage/files_to_instrument.txt"\n'
        'use_remoteexec = true'
      ),
    ),
    api.post_process(post_process.MustRun, 'write cleaned gn args'),
    api.post_process(
      post_process.StepCommandContains,
      'write cleaned gn args',
      ['use_remoteexec = true'],
    ),
    api.post_process(
      post_process.StepCommandDoesNotContain,
      'write cleaned gn args',
      ['coverage_instrumentation_input_file'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'code_coverage_instrument_everything_builder_gn_args',
    boilerplate(
      build_dir='[CACHE]/src/out/Release',
      builder_recipe='chromium_trybot',
      bypass_branch_check=True,
      skip_instrumentation=False,
      preserve_gn_args=False,
    ),
    api.code_coverage(use_clang_coverage=True),
    api.step_data(
      'lookup_builder_gn_args',
      stdout=api.raw_io.output_text(
        'coverage_instrumentation_input_file = '
        '".code-coverage/files_to_instrument.txt"\n'
        'b = true'
      ),
    ),
    api.post_process(post_process.MustRun, 'write cleaned gn args'),
    api.post_process(
      post_process.StepCommandContains, 'write cleaned gn args', ['b = true']
    ),
    api.post_process(
      post_process.StepCommandDoesNotContain,
      'write cleaned gn args',
      ['coverage_instrumentation_input_file'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'multiple_tests',
    boilerplate(
      test_names=['browser_tests', 'unit_tests'],
      additional_test_args=['--gtest_repeat=100'],
      target_spec={
        'fake-tester': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
              },
            },
            {
              'name': 'unit_tests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
              },
            },
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(post_process.MustRun, 'isolate tests'),
    api.post_process(
      post_process.MustRun, 'test_pre_run.[trigger] browser_tests'
    ),
    api.post_process(post_process.MustRun, 'test_pre_run.[trigger] unit_tests'),
    api.post_process(
      post_process.StepCommandContains,
      'generate_build_files',
      ['-m', 'fake-group', '-b', 'fake-tester'],
    ),
    api.post_process(post_process.MustRun, 'browser_tests'),
    api.post_process(post_process.MustRun, 'unit_tests'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'gpu_model_backwards_compatibility_known_vendor',
    boilerplate(
      test_names=['browser_tests on NVIDIA 0x1234 GPU on Linux'],
      target_spec={
        'fake-tester': {
          'gtest_tests': [
            {
              # Simulate a step name from before GPU models were added.
              'name': 'browser_tests on NVIDIA GPU on Linux',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
              },
            }
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(post_process.MustRun, 'isolate tests'),
    api.post_process(
      post_process.MustRun,
      'test_pre_run.[trigger] browser_tests on NVIDIA GPU on Linux',
    ),
    api.post_process(
      post_process.MustRun, 'browser_tests on NVIDIA GPU on Linux'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'gpu_model_backwards_compatibility_unknown_vendor',
    boilerplate(
      test_names=['browser_tests on (1234:5678) GPU on Linux'],
      target_spec={
        'fake-tester': {
          'gtest_tests': [
            {
              # Simulate a step name from before GPU models were added.
              'name': 'browser_tests on (1234) GPU on Linux',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
              },
            }
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(post_process.MustRun, 'isolate tests'),
    api.post_process(
      post_process.MustRun,
      'test_pre_run.[trigger] browser_tests on (1234) GPU on Linux',
    ),
    api.post_process(
      post_process.MustRun, 'browser_tests on (1234) GPU on Linux'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'gpu_model_backwards_compatibility_multiple_vendors_first_known',
    boilerplate(
      test_names=['browser_tests on NVIDIA 0x1234/(1234:5678) GPU on Linux'],
      target_spec={
        'fake-tester': {
          'gtest_tests': [
            {
              # Simulate a step name from before GPU models were added.
              'name': 'browser_tests on NVIDIA GPU on Linux',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
              },
            }
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(post_process.MustRun, 'isolate tests'),
    api.post_process(
      post_process.MustRun,
      'test_pre_run.[trigger] browser_tests on NVIDIA GPU on Linux',
    ),
    api.post_process(
      post_process.MustRun, 'browser_tests on NVIDIA GPU on Linux'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'gpu_model_backwards_compatibility_multiple_vendors_first_unknown',
    boilerplate(
      test_names=['browser_tests on (1234:5678)/NVIDIA 0x1234 GPU on Linux'],
      target_spec={
        'fake-tester': {
          'gtest_tests': [
            {
              # Simulate a step name from before GPU models were added.
              'name': 'browser_tests on (1234) GPU on Linux',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
              },
            }
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(post_process.MustRun, 'isolate tests'),
    api.post_process(
      post_process.MustRun,
      'test_pre_run.[trigger] browser_tests on (1234) GPU on Linux',
    ),
    api.post_process(
      post_process.MustRun, 'browser_tests on (1234) GPU on Linux'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'SwarmingIsolatedScriptTest_skips_upload',
    boilerplate(
      test_names=['fake-script-test'],
      target_spec={
        'fake-tester': {
          'isolated_scripts': [
            {
              'name': 'fake-script-test',
              'script': 'fake-script',
              'results_handler': 'layout tests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                },
              },
            }
          ],
        }
      },
    ),
    api.post_process(
      post_process.DoesNotRun, 'archive results for fake-script-test'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ScriptTest_skips_compile',
    boilerplate(
      test_names=['check_network_annotations'],
      target_spec={
        'fake-tester': {
          'scripts': [
            {
              'name': 'check_network_annotations',
              'script': 'check_network_annotations.py',
            }
          ],
        }
      },
    ),
    api.post_process(post_process.DoesNotRun, 'compile'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'ScriptTest_fails',
    boilerplate(
      test_names=['browser_tests', 'check_network_annotations'],
      target_spec={
        'fake-tester': {
          'scripts': [
            {
              'name': 'check_network_annotations',
              'script': 'check_network_annotations.py',
            }
          ],
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
              },
            }
          ],
        }
      },
    ),
    api.override_step_data(
      'check_network_annotations',
      retcode=1,
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'disabled_remote_options',
    boilerplate(no_rbe=True, no_siso=True),
    api.post_process(post_process.MustRun, 'generate .isolate files'),
    api.post_process(
      post_process.StepCommandContains,
      'write cleaned gn args',
      ['target_cpu = "x86"\nuse_remoteexec = false\nuse_siso = false'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'mb_phase',
    boilerplate(
      ctbc_properties=gen_ctbc_properties(
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
          mb_phase_for_tests='some-mb-phase',
        ),
      ),
    ),
    api.post_process(
      post_process.StepCommandContains, 'lookup GN args', ['some-mb-phase']
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'cog',
    boilerplate(
      checkout_path='/google/cog/cloud/src',
      preserve_gn_args=True,
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'gemini',
    boilerplate(run_type=Request.RunType.RUN_TYPE_COMPILE),
    api.properties.environ(
      GEMINI_CLI='1',
    ),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'antigravity',
    boilerplate(run_type=Request.RunType.RUN_TYPE_COMPILE),
    api.properties.environ(
      ANTIGRAVITY_AGENT='1',
    ),
    api.post_process(post_process.MustRun, 'compile'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'compile_remote_linking',
    boilerplate(run_type=Request.RunType.RUN_TYPE_COMPILE),
    api.siso.properties(output_local_strategy='minimum'),
    api.path.exists(
      api.path.cache_dir / 'src/out/Release/browser_tests.runtime_deps'
    ),
    api.step_data(
      'download compilation outputs.read browser_tests.runtime_deps',
      api.file.read_text('output_file1\noutput_file2'),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'download compilation outputs.fetch browser_tests',
      ['output_file1', 'output_file2'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'autotest_failure',
    boilerplate(test_names=['path/to/a/test.cc']),
    api.path.exists(api.path.cache_dir / 'src/path/to/a/test.cc'),
    api.override_step_data('invoke autotest.py', retcode=1),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'cross_compile_checks_default_target_os_gclient',
    api.properties(
      checkout_path='[CACHE]/src',
      run_type=Request.RunType.RUN_TYPE_COMPILE_AND_RUN,
      rerun_options=Request.RerunOptions(skip_config_validation=True),
    ),
    api.platform('linux', 64),
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              chromium_config_kwargs={
                'TARGET_PLATFORM': 'ios',
              },
            ),
          },
        }
      ),
    ),
    api.path.exists(api.path.cache_dir.joinpath('.gclient')),
    api.step_data(
      'read gclient',
      api.file.read_text("""
solutions = [
  {
    'url': 'https://chromium.googlesource.com/chromium/src.git',
    'custom_vars': {
      'checkout_telemetry_dependencies': 'False',
    },
  },
]
target_os=['os']
"""),
    ),
    api.post_process(
      post_process.SummaryMarkdown,
      'Caution: your .gclient file and the builder\'s mismatches in the '
      'following way(s). Please run "gclient sync" after resolving these:'
      '\n- target_os in builder config `"[\'mac\']"` is not in the local '
      '.gclient file. Set it to: `target_os = ["os", "mac"]`',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'cross_compile_checks_default_target_os_gclient_accepts_os_alias',
    api.properties(
      checkout_path='[CACHE]/src',
      run_type=Request.RunType.RUN_TYPE_COMPILE_AND_RUN,
      rerun_options=Request.RerunOptions(skip_config_validation=True),
    ),
    api.platform('linux', 64),
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              chromium_config_kwargs={
                'TARGET_PLATFORM': 'ios',
              },
            ),
          },
        }
      ),
    ),
    api.path.exists(api.path.cache_dir.joinpath('.gclient')),
    api.step_data(
      'read gclient',
      api.file.read_text("""
solutions = [
  {
    'url': 'https://chromium.googlesource.com/chromium/src.git',
    'custom_vars': {
      'checkout_telemetry_dependencies': 'False',
    },
  },
]
target_os=['osx']
"""),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'cross_compile_checks_default_target_os_gclient_accepts_default',
    api.properties(
      checkout_path='[CACHE]/src',
      run_type=Request.RunType.RUN_TYPE_COMPILE_AND_RUN,
      rerun_options=Request.RerunOptions(skip_config_validation=True),
    ),
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake-group',
      builder='fake-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake-group': {
            'fake-builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              chromium_config_kwargs={
                'TARGET_PLATFORM': 'ios',
              },
            ),
          },
        }
      ),
    ),
    api.platform('mac', 64),
    api.path.exists(api.path.cache_dir.joinpath('.gclient')),
    api.step_data(
      'read gclient',
      api.file.read_text("""
solutions = [
  {
    'url': 'https://chromium.googlesource.com/chromium/src.git',
    'custom_vars': {
      'checkout_telemetry_dependencies': 'False',
    },
  },
]
target_os=[]
"""),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'single_test_case_filter_and_override',
    boilerplate(
      test_names=['browser_tests'],
      omit_default_test_args=True,
      additional_test_args=['--gtest_filter=Foo.Bar'],
      target_spec={
        'fake-tester': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
                'shards': 20,
              },
            }
          ],
        },
      },
    ),
    # Single test case filter should trigger one shard.
    api.post_process(
      post_process.MustRun, 'test_pre_run.[trigger] browser_tests'
    ),
    api.post_process(
      post_process.DoesNotRun, 'test_pre_run.[trigger] browser_tests (2)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'many_test_case_filter',
    boilerplate(
      test_names=['browser_tests'],
      additional_test_args=['--gtest_filter=Foo.*'],
      target_spec={
        'fake-tester': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'swarming': {
                'dimensions': {
                  'os': 'Linux',
                  'pool': 'fake-pool',
                },
                'shards': 20,
              },
            }
          ],
        },
      },
    ),
    # Wildcard test case filter should trigger multiple shards.
    api.post_process(
      post_process.MustRun, 'test_pre_run.[trigger] browser_tests'
    ),
    api.post_process(
      post_process.MustRun, 'test_pre_run.[trigger] browser_tests (2)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'jj_workspace_success',
    boilerplate(),
    api.step_data('git rev-parse', retcode=1),
    api.step_data('jj root', retcode=0),
    api.step_data('get jj revision', stdout=api.raw_io.output('jj_commit_id')),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'jj_workspace_not_jj',
    boilerplate(target_spec=False),
    api.step_data('git rev-parse', retcode=1),
    api.step_data('jj root', retcode=1),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'jj_workspace_log_fail',
    boilerplate(target_spec=False),
    api.step_data('git rev-parse', retcode=1),
    api.step_data('jj root', retcode=0),
    api.step_data('get jj revision', retcode=1),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
