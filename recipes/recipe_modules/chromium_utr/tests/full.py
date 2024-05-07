# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

from PB.recipe_modules.build.chromium_utr.request import Request
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'chromium_utr',
    'code_coverage',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
]

PROPERTIES = Request


def RunSteps(api, request):
  builder = api.buildbucket.build.builder.builder
  builder_id = chromium.BuilderId.create_for_group(
      api.m.properties['builder_group'], builder)

  _, builder_config = (
      api.chromium_tests_builder_config.lookup_builder(use_try_db=True))
  api.path.checkout_dir = api.path.abs_to_path(request.checkout_path)
  build_path = api.path.checkout_dir.joinpath('out', 'Release')
  api.chromium_tests.configure_build(builder_config)

  result = api.chromium_utr.prerun_checks(request, build_path, builder_id)
  if result:
    return result

  api.chromium_utr.generate_got_revisions_map()
  api.chromium_utr.handle_code_coverage(build_path, request, builder_id)
  api.chromium_utr.get_remote_compile_options(build_path)


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  def gen_ctbc_properties(builder_spec=None):
    return ctbc_api.properties(
        ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
            builder_group='fake-group',
            builder='fake-builder',
            builder_spec=builder_spec,
        ).with_mirrored_tester(
            builder_group='fake-group',
            builder='fake-tester',
        ).assemble())

  def boilerplate_properties(
      checkout_path='[CACHE]/src',
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
    return api.properties(
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

  def boilerplate(build=None, ctbc_properties=None, **kwargs):
    return sum([
        boilerplate_properties(**kwargs),
        ctbc_properties if ctbc_properties else gen_ctbc_properties(
            builder_spec=ctbc.BuilderSpec.create(
                gclient_config='chromium',
                chromium_config='chromium',
            ),),
        build or api.chromium.generic_build(
            builder_group='fake-group',
            builder='fake-tester',
        ),
    ], api.empty_test_data())

  yield api.test(
      'basic',
      boilerplate(
          preserve_gn_args=False,
          bypass_gclient=False,
      ),
      api.path.exists(api.path.cache_dir / '.gclient'),
      api.step_data(
          'read gclient',
          api.file.read_text("""
solutions = [
  {
    'url': 'https://chromium.googlesource.com/chromium/src.git',
    'custom_vars': {
      'checkout_telemetry_dependencies': True,
    },
  },
]
""")),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'missing_gclient',
      boilerplate(bypass_gclient=False),
      api.expect_exception('FileNotFoundError'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'src_not_in_gclient',
      boilerplate(bypass_gclient=False),
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
""")),
      api.post_process(
          post_process.ResultReason,
          'Caution: your .gclient file could not be validated. Exactly one '
          'solution with \'url\' set to '
          'https://chromium.googlesource.com/chromium/src.git must be set\n'),
      api.post_process(post_process.StepCommandContains, 'read gclient',
                       ['[CACHE]/.gclient']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'bad_gclient',
      boilerplate(
          preserve_gn_args=False,
          bypass_gclient=False,
          ctbc_properties=gen_ctbc_properties(
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='ios',
                  chromium_config='chromium',
              ),),
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
""")),
      api.post_process(
          post_process.ResultReason,
          'Caution: your .gclient file and the builder\'s mismatches in the '
          'following way(s). Please run "gclient sync" after resolving these:\n'
          '- rbe_instance has been set in the .gclient file\n'
          '- custom_var checkout_telemetry_dependencies has mismatched value '
          'in the local .gclient file. Set it to: '
          '`"checkout_telemetry_dependencies": "True"`\n'
          '- target_os in builder config `"[\'ios\']"` is not in the local '
          '.gclient file. Set it to: `target_os = ["os", "ios"]`'),
      api.post_process(post_process.StepCommandContains, 'read gclient',
                       ['[CACHE]/.gclient']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'bad_gn_args',
      boilerplate(bypass_gn_args=False, build_dir='[CACHE]/src/out/Release'),
      api.path.exists(api.path.cache_dir / 'src/out/Release/args.gn'),
      api.step_data(
          'lookup_builder_gn_args',
          stdout=api.raw_io.output_text('import("//builder.args")\n'
                                        'a = "1"\n'
                                        'c = true\n'
                                        'd = true')),
      api.step_data(
          'read GN args',
          api.raw_io.output_text('import("//local.args")\n'
                                 'b="2"\n'
                                 'c=false\n'
                                 'd=true')),
      api.post_process(
          post_process.ResultReason,
          'Caution: your build\'s gn args and the builder\'s mismatches in the '
          'following way(s):\n'
          '- `b` in current build dir is not set by the builder\n'
          '- `c` in current build (`false`) does not match builder value '
          '(`true`)\n'
          '- `a` set by the builder is absent in the current build dir\n'
          '- `import("//builder.args")` used by the builder is absent in the '
          'current build dir\n'
          '- `import("//local.args")` in current build dir is not used by the '
          'builder'),
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
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'code_coverage_no_upstream',
      boilerplate(builder_recipe='chromium_trybot',),
      api.code_coverage(use_clang_coverage=True),
      api.step_data(
          'check upstream branch',
          retcode=1,
      ),
      api.post_process(
          post_process.ResultReason,
          'Caution: failed to get an upstream branch from the current checkout'
      ),
      api.post_process(
          post_process.MustRun,
          'write output_properties_file checkout/output_properties.json'),
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
      api.post_process(post_process.StatusSuccess),
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
          api.path.cache_dir.joinpath('src', 'out', 'Release', 'args.gn')),
      api.step_data(
          'read GN args',
          api.raw_io.output_text('coverage_instrumentation_input_file = '
                                 '".code-coverage/files_to_instrument.txt"\n'
                                 'use_remoteexec = true')),
      api.post_process(post_process.MustRun,
                       'remove coverage_instrumentation_input_file gn arg'),
      api.post_process(post_process.StepCommandContains,
                       'remove coverage_instrumentation_input_file gn arg',
                       ['use_remoteexec = true']),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'remove coverage_instrumentation_input_file gn arg',
                       ['coverage_instrumentation_input_file']),
      api.post_process(post_process.StatusSuccess),
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
          'read GN args',
          api.raw_io.output_text('coverage_instrumentation_input_file = '
                                 '".code-coverage/files_to_instrument.txt"\n'
                                 'b = true')),
      api.step_data(
          'lookup_builder_gn_args_for_code_coverage',
          stdout=api.raw_io.output_text(
              'coverage_instrumentation_input_file = '
              '".code-coverage/files_to_instrument.txt"\n'
              'b = true')),
      api.post_process(post_process.MustRun,
                       'remove coverage_instrumentation_input_file gn arg'),
      api.post_process(post_process.StepCommandContains,
                       'remove coverage_instrumentation_input_file gn arg',
                       ['b = true']),
      api.post_process(post_process.StepCommandDoesNotContain,
                       'remove coverage_instrumentation_input_file gn arg',
                       ['coverage_instrumentation_input_file']),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
