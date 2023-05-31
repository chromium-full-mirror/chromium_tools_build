# Copyright 2014 The Chromium Authors. All rights reserved.
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

# TODO(crbug/1369919):
# This recipe is deprecated - use chromium/fuzz.py instead.
# This recipe can be removed when builders in all relevant branches
# have migrated.


@attrs()
class LibfuzzerSpec(chromium.BuilderSpec):

  archive_prefix = attrib(str, default='libfuzzer')
  v8_targets_only = attrib(bool, default=False)
  ios_targets_only = attrib(bool, default=False)
  upload_bucket = attrib(str)
  upload_directory = attrib(str)


BUILDERS = freeze({
    'chromium.fuzz': {
        'builders': {
            'Libfuzzer Upload Chrome OS ASan':
                LibfuzzerSpec.create(
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
                LibfuzzerSpec.create(
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
                LibfuzzerSpec.create(
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
                LibfuzzerSpec.create(
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
                LibfuzzerSpec.create(
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
                LibfuzzerSpec.create(
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
                LibfuzzerSpec.create(
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
                LibfuzzerSpec.create(
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
                LibfuzzerSpec.create(
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
                LibfuzzerSpec.create(
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
                LibfuzzerSpec.create(
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
                LibfuzzerSpec.create(
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
                LibfuzzerSpec.create(
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
        },
    },
})


def RunSteps(api):
  builder_id, bot_config = api.chromium.configure_bot(BUILDERS, ['mb'])

  checkout_results = api.chromium_checkout.ensure_checkout(bot_config)

  api.chromium.ensure_toolchains()
  api.chromium.runhooks()
  api.chromium.mb_gen(
      builder_id, use_goma=False, use_reclient=True)

  with api.context(cwd=api.path['checkout'], env=api.chromium.get_env()):
    all_fuzzers = api.gn.refs(
        api.chromium.output_dir, ['//testing/libfuzzer:libfuzzer_main'],
        output_type='executable',
        output_format='output',
        step_name='calculate all_fuzzers',
        step_test_data=lambda: api.raw_io.test_api.stream_output_text(
            'target1\ntarget2\nv8_target3', stream='stdout'))
    if bot_config.v8_targets_only:
      # Some builders only need the V8 targets as the only difference is code
      # generated by V8 simulators.
      v8_fuzzers = api.gn.refs(
          api.chromium.output_dir, ['//v8:fuzzer_support'],
          output_type='executable',
          output_format='output',
          step_name='calculate v8_fuzzers',
          step_test_data=lambda: api.raw_io.test_api.stream_output_text(
              'v8_target3', stream='stdout'))
      all_fuzzers = all_fuzzers & v8_fuzzers
    elif bot_config.ios_targets_only:
      ios_fuzzers = api.gn.refs(
          api.chromium.output_dir,
          ['//testing/libfuzzer:build_for_ios_clusterfuzz_job'],
          output_type='executable',
          output_format='output',
          step_name='calculate ios_fuzzers',
          step_test_data=lambda: api.raw_io.test_api.stream_output_text(
              'ios_target', stream='stdout'))
      all_fuzzers = all_fuzzers & ios_fuzzers
    no_clusterfuzz = api.gn.refs(
        api.chromium.output_dir, ['//testing/libfuzzer:no_clusterfuzz'],
        output_type='executable',
        output_format='output',
        step_name='calculate no_clusterfuzz',
        step_test_data=lambda: api.raw_io.test_api.stream_output_text(
            'target1', stream='stdout'))
  targets = sorted(all_fuzzers - no_clusterfuzz)

  # For iOS, the target list from |api.gn.refs| is a list of paths like
  # obj/.../XXX_fuzzer. The last part of the path is the target name to be
  # compiled.
  if api.chromium.c.TARGET_PLATFORM == 'ios':
    targets = [target.split('/')[-1] for target in targets]

  api.step.active_result.presentation.logs['all_fuzzers'] = sorted(all_fuzzers)
  api.step.active_result.presentation.logs['no_clusterfuzz'] = (
      sorted(no_clusterfuzz))
  api.step.active_result.presentation.logs['targets'] = targets
  raw_result = api.chromium.compile(
      targets=targets,
      use_goma_module=False,
      use_reclient=True)
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

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
