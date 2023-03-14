# Copyright 2018 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.engine_types import freeze

from RECIPE_MODULES.build import chromium

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/tryserver',
    'filter',
    'gn',
    'recipe_engine/context',
    'recipe_engine/json',
    'recipe_engine/raw_io',
    'reclient',
]

BUILDERS = freeze({
    'tryserver.chromium.linux': {
        'builders': {
            'linux-libfuzzer-asan-rel':
                chromium.BuilderSpec.create(
                    chromium_config='chromium',
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                ),
        },
    },
    'tryserver.chromium.win': {
        'builders': {
            'win-libfuzzer-asan-rel':
                chromium.BuilderSpec.create(
                    chromium_config='chromium_clang',
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'win',
                        'TARGET_BITS': 64,
                    },
                ),
        },
    },
})

INCLUDED_FUZZ_TARGETS = ['//testing/libfuzzer:libfuzzer_main']
EXCLUDED_FUZZ_TARGETS = ['//testing/libfuzzer:no_clusterfuzz']

# TODO(crbug/1369919):
# This recipe is deprecated - use chromium_fuzz_engine instead.
# This recipe can be removed when builders in all relevant branches
# have migrated.


def RunSteps(api):
  assert api.tryserver.is_tryserver

  with api.chromium.chromium_layout():
    builder_id, bot_config = api.chromium.configure_bot(BUILDERS, ['mb'])

    api.chromium_checkout.ensure_checkout(bot_config)
    checkout_dir = api.chromium_checkout.src_dir
    use_goma_module = True  # by default
    use_reclient = False
    with api.context(cwd=checkout_dir):
      outdir = api.chromium.output_dir

      api.chromium.runhooks()
      api.chromium.ensure_goma()
      gn_args_str = api.chromium.mb_gen(builder_id)

      gn_args = api.gn.parse_gn_args(gn_args_str)
      use_reclient = gn_args.get('use_remoteexec') == 'true'
      use_goma_module = not use_reclient and gn_args.get('use_goma') == 'true'

      # Calculate the GN labels of all fuzz targets.
      all_fuzz_labels = api.gn.refs(
          outdir, INCLUDED_FUZZ_TARGETS, output_type='executable',
          step_name='calculate all_fuzzers', output_format='label')
      excluded_fuzz_labels = api.gn.refs(
          outdir, EXCLUDED_FUZZ_TARGETS, output_type='executable',
          step_name='calculate no_fuzzers', output_format='label')
      fuzz_labels = sorted(all_fuzz_labels - excluded_fuzz_labels)

      # Filter out all targets that the patch doesn't affect.
      affected_files = api.chromium_checkout.get_files_affected_by_patch()
      test_targets, compile_targets = api.filter.analyze(
          affected_files, None, fuzz_labels)
      affected_fuzz_labels = sorted(test_targets + compile_targets)
      if not affected_fuzz_labels:
        return

      # Run MB one more time since filter calls above wipes out the specified
      # goma dir.
      api.chromium.mb_gen(
          builder_id,
          use_goma=use_goma_module,
          use_reclient=use_reclient,
          gn_args_location=api.gn.LOGS)

      # Convert the GN labels to ninja targets and pass them into compile.
      affected_fuzz_targets = list(
          api.gn.ls(outdir, affected_fuzz_labels, output_format='output'))
      return api.chromium.compile(
          targets=affected_fuzz_targets,
          use_goma_module=use_goma_module,
          use_reclient=use_reclient)


def GenTests(api):
  yield api.test(
      'basic_linux_tryjob_no_compile',
      api.chromium.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux-libfuzzer-asan-rel'),
      api.step_data(
          'calculate all_fuzzers',
          stdout=api.raw_io.output_text(
              '//foo/bar:target1\n//foo/bar:target2\n//foo/bar:target3')),
      api.step_data(
          'calculate no_fuzzers',
          stdout=api.raw_io.output_text('//foo/bar:target1')),
      api.filter.no_dependency(),
  )
  gn_args_reclient = '\n'.join((
      'goma_dir = "/b/build/slave/cache/goma_client"',
      'target_cpu = "x86"',
      'use_remoteexec = true',
  ))

  yield api.test(
      'basic_linux_tryjob_with_compile',
      api.chromium.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux-libfuzzer-asan-rel'),
      api.step_data(
          'calculate all_fuzzers',
          stdout=api.raw_io.output_text('\n'.join(
              ['//foo/bar:target1', '//foo/bar:target2',
               '//foo/bar:target3']))),
      api.step_data(
          'calculate no_fuzzers',
          stdout=api.raw_io.output_text('//foo/bar:target1')),
      api.filter.analyze_output(
          status='Found dependency',
          test_targets=[],
          compile_targets=['//foo/bar:target2'],
      ),
      api.step_data(
          'list gn targets', stdout=api.raw_io.output_text('target2')),
  )

  yield api.test(
      'basic_linux_tryjob_with_compile_using_reclient',
      api.chromium.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux-libfuzzer-asan-rel'),
      api.step_data(
          'calculate all_fuzzers',
          stdout=api.raw_io.output_text('\n'.join(
              ['//foo/bar:target1', '//foo/bar:target2',
               '//foo/bar:target3']))),
      api.reclient.properties(instance='rbe-chromium-untrusted'),
      api.step_data(
          'lookup GN args', stdout=api.raw_io.output_text(gn_args_reclient)),
      api.step_data(
          'calculate no_fuzzers',
          stdout=api.raw_io.output_text('//foo/bar:target1')),
      api.filter.analyze_output(
          status='Found dependency',
          test_targets=[],
          compile_targets=['//foo/bar:target2'],
      ),
      api.step_data(
          'list gn targets', stdout=api.raw_io.output_text('target2')),
  )

  yield api.test(
      'compile_failure',
      api.chromium.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux-libfuzzer-asan-rel'),
      api.step_data(
          'calculate all_fuzzers',
          stdout=api.raw_io.output_text('\n'.join(
              ['//foo/bar:target1', '//foo/bar:target2',
               '//foo/bar:target3']))),
      api.step_data(
          'calculate no_fuzzers',
          stdout=api.raw_io.output_text('//foo/bar:target1')),
      api.filter.analyze_output(
          status='Found dependency',
          test_targets=[],
          compile_targets=['//foo/bar:target2'],
      ),
      api.step_data('compile', retcode=1),
      api.post_process(post_process.StatusFailure),
      api.post_process(post_process.DropExpectation),
  )
