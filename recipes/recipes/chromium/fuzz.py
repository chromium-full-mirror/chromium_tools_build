# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re
from recipe_engine import post_process
from recipe_engine.engine_types import freeze
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build.attr_utils import attrs, attrib

DEPS = [
    'archive',
    'chromium',
    'chromium_checkout',
    'depot_tools/depot_tools',
    'depot_tools/tryserver',
    'filter',
    'gn',
    'reclient',
    'recipe_engine/context',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]


@attrs()
class FuzzEngineSpec(chromium.BuilderSpec):

  archive_prefix = attrib(str, default='libfuzzer')
  v8_targets_only = attrib(bool, default=False)
  ios_targets_only = attrib(bool, default=False)
  upload_bucket = attrib(str, default=None)
  upload_directory = attrib(str, default=None)


BUILDERS = freeze({
    'chromium.fuzz': {
        'builders': {
            'Libfuzzer Upload Chrome OS ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'chromeos',
                        'TARGET_BITS': 64,
                    },
                    gclient_apply_config=['chromeos'],
                    archive_prefix='libfuzzer-chromeos',
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='chromeos-asan',
                ),
            'Libfuzzer Upload iOS Catalyst Debug':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=[
                        'clobber',
                        'mac_toolchain',
                    ],
                    gclient_apply_config=['ios'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Debug',
                        'TARGET_BITS': 64,
                        'TARGET_PLATFORM': 'ios',
                        'HOST_PLATFORM': 'mac',
                    },
                    archive_prefix='libfuzzer-ios',
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='ios-catalyst-debug',
                    ios_targets_only=True,
                ),
            'Libfuzzer Upload Linux32 ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 32,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan',
                ),
            'Libfuzzer Upload Linux32 V8-ARM ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 32,
                    },
                    archive_prefix='libfuzzer-v8-arm',
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan-arm-sim',
                    v8_targets_only=True,
                ),
            'Libfuzzer Upload Linux32 V8-ARM ASan Debug':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Debug',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 32,
                    },
                    archive_prefix='libfuzzer-v8-arm',
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan-arm-sim',
                    v8_targets_only=True,
                ),
            'Libfuzzer Upload Linux ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan',
                ),
            'Libfuzzer Upload Linux ASan Debug':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Debug',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan',
                ),
            'Libfuzzer Upload Linux V8-ARM64 ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    archive_prefix='libfuzzer-v8-arm64',
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan-arm64-sim',
                    v8_targets_only=True,
                ),
            'Libfuzzer Upload Linux V8-ARM64 ASan Debug':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Debug',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    archive_prefix='libfuzzer-v8-arm64',
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan-arm64-sim',
                    v8_targets_only=True,
                ),
            'Libfuzzer Upload Linux MSan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber', 'msan'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='msan',
                ),
            'Libfuzzer Upload Linux UBSan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='ubsan',
                ),
            'Libfuzzer Upload Mac ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'mac',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan',
                ),
            'Libfuzzer Upload Windows ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'win',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-libfuzzer',
                    upload_directory='asan',
                ),
            'Afl Upload Linux ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-afl',
                    upload_directory='asan',
                    archive_prefix='afl',
                ),
            'Centipede Upload Linux ASan':
                FuzzEngineSpec.create(
                    chromium_config='chromium_clang',
                    chromium_apply_config=['clobber'],
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    upload_bucket='chromium-browser-centipede',
                    upload_directory='asan',
                    archive_prefix='centipede',
                ),
        },
    },
    'tryserver.chromium.linux': {
        'builders': {
            'linux-libfuzzer-asan-rel':
                FuzzEngineSpec.create(
                    chromium_config='chromium',
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                ),
            'linux-afl-asan-rel':
                FuzzEngineSpec.create(
                    chromium_config='chromium',
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                ),
            'linux-centipede-asan-rel':
                FuzzEngineSpec.create(
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
                FuzzEngineSpec.create(
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


def gn_refs(api, step_name, target):
  """Runs gn refs to calculate targets depending on target.
  Returns: the list of matched targets.
  """
  return api.gn.refs(
      api.chromium.output_dir, [target],
      output_type='executable',
      step_name=step_name,
      output_format='label')


def RunSteps(api):
  builder_id, bot_config = api.chromium.configure_bot(BUILDERS, ['mb'])

  checkout_results = api.chromium_checkout.ensure_checkout(bot_config)

  use_reclient = bool(api.reclient.instance)

  api.chromium.ensure_toolchains()
  api.chromium.runhooks()
  api.chromium.mb_gen(builder_id)

  with api.context(cwd=api.path['checkout'], env=api.chromium.get_env()):
    all_fuzzers = gn_refs(api, 'calculate all_fuzzers',
                          '//testing/libfuzzer:fuzzing_engine')
    if bot_config.v8_targets_only:
      # Some builders only need the V8 targets as the only difference is code
      # generated by V8 simulators.
      v8_fuzzers = gn_refs(api, 'calculate v8_fuzzers', '//v8:fuzzer_support')
      all_fuzzers = all_fuzzers & v8_fuzzers
    elif bot_config.ios_targets_only:
      ios_fuzzers = gn_refs(
          api, 'calculate ios_fuzzers',
          '//testing/libfuzzer:build_for_ios_clusterfuzz_job')
      all_fuzzers = all_fuzzers & ios_fuzzers
    no_clusterfuzz = gn_refs(api, 'calculate no_clusterfuzz',
                             '//testing/libfuzzer:no_clusterfuzz')
    targets = sorted(all_fuzzers - no_clusterfuzz)

    api.step.active_result.presentation.logs['all_fuzzers'] = sorted(
        all_fuzzers)
    api.step.active_result.presentation.logs['no_clusterfuzz'] = (
        sorted(no_clusterfuzz))
    api.step.active_result.presentation.logs['targets'] = targets

    outdir = api.chromium.output_dir
    if api.tryserver.is_tryserver:
      # Filter out all targets that the patch doesn't affect.
      affected_files = api.chromium_checkout.get_files_affected_by_patch()
      test_targets, compile_targets = api.filter.analyze(
          affected_files, None, targets)
      targets = sorted(test_targets + compile_targets)
      if not targets:
        return

      # Run MB one more time since filter calls above wipes out the specified
      # goma dir.
      api.chromium.mb_gen(builder_id, gn_args_location=api.gn.LOGS)

    # Up until now we work in terms of GN labels so that we can use
    # api.filter.analyze above in the trybot case. We now convert the GN labels to
    # ninja targets and pass them into compile.
    targets = list(api.gn.ls(outdir, targets, output_format='output'))

    # For iOS, the target list from |api.gn.refs| is a list of paths like
    # obj/.../XXX_fuzzer. The last part of the path is the target name to be
    # compiled.
    if api.chromium.c.TARGET_PLATFORM == 'ios':
      targets = [target.split('/')[-1] for target in targets]

    raw_result = api.chromium.compile(
        targets=targets,
        use_goma_module=not use_reclient,
        use_reclient=use_reclient)
    if raw_result.status != common_pb.SUCCESS or api.tryserver.is_tryserver:
      return raw_result
    assert (bot_config.upload_directory is not None)
    assert (bot_config.upload_bucket is not None)

    # Make sure 32 bit archives are distinguished from 64 bit ones.
    kwargs = {}
    if api.chromium.c.TARGET_BITS == 32:
      kwargs['use_legacy'] = False
      kwargs['bitness'] = 32

    api.archive.clusterfuzz_archive(
        build_dir=api.chromium.output_dir,
        update_properties=checkout_results.json.output['properties'],
        gs_bucket=bot_config.upload_bucket,
        archive_prefix=bot_config.archive_prefix,
        archive_subdir_suffix=bot_config.upload_directory,
        gs_acl='public-read',
        **kwargs)


def GenTests(api):
  for test in api.chromium.gen_tests_for_builders(BUILDERS):
    test += api.reclient.properties()
    test += api.step_data(
        'calculate all_fuzzers',
        stdout=api.raw_io.output_text('target1 target2 target3')
    ) + api.step_data(
        'calculate no_clusterfuzz', stdout=api.raw_io.output_text('target1'))
    if not "tryserver" in test.name:
      test += api.post_process(post_process.MustRun, 'gsutil upload')
    test += api.post_process(post_process.DropExpectation)
    if 'Upload_iOS' in test.name:
      yield (test + api.properties(xcode_build_version='12345'))
    else:
      yield test

  yield api.test(
      'compile_failure',
      api.chromium.ci_build(
          builder_group='chromium.fuzz',
          builder='Libfuzzer Upload Mac ASan',
      ),
      api.platform.name('mac'),
      api.reclient.properties(),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

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
          'calculate no_clusterfuzz',
          stdout=api.raw_io.output_text('//foo/bar:target1')),
      api.filter.no_dependency(),
  )

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
          'calculate no_clusterfuzz',
          stdout=api.raw_io.output_text('//foo/bar:target1')),
      api.filter.analyze_output(
          status='Found dependency',
          test_targets=[],
          compile_targets=['//foo/bar:target2'],
      ),
      api.step_data(
          'list gn targets', stdout=api.raw_io.output_text('target2')),
  )

  gn_args_reclient = '\n'.join((
      'goma_dir = "/b/build/slave/cache/goma_client"',
      'target_cpu = "x86"',
      'use_remoteexec = true',
  ))

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
          'calculate no_clusterfuzz',
          stdout=api.raw_io.output_text('//foo/bar:target1')),
      api.filter.analyze_output(
          status='Found dependency',
          test_targets=[],
          compile_targets=['//foo/bar:target2'],
      ),
      api.step_data(
          'list gn targets', stdout=api.raw_io.output_text('target2')),
  )
