# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe to compare reclient based builds against a goma based build.

Waterfall page: https://build.chromium.org/p/chromium.swarm/waterfall

"""

from recipe_engine import post_process
from recipe_engine.engine_types import freeze
from recipe_engine.recipe_api import Property
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build import chromium

DEPS = [
    'builder_group',
    'chromium',
    'chromium_checkout',
    'depot_tools/gclient',
    'isolate',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'reclient',
]

COMPARISON_BUILDERS = freeze({
    'Comparison Android (reclient)': {
        'chromium_config': 'android',
        'gclient_config': 'chromium',
        'chromium_apply_config': [
            'mb', 'goma_enable_cache_silo', 'goma_large_cache_file',
            'download_vr_test_apks'
        ],
        'gclient_apply_config': ['android', 'reclient_test'],
        'chromium_config_kwargs': {
            'BUILD_CONFIG': 'Debug',
            'TARGET_BITS': 32,
            'TARGET_PLATFORM': 'android',
        },
        'android_config': 'main_builder_mb',
        'simulation_platform': 'linux',
        'platform': 'linux',
        'targets': ['all'],
    },
    'Comparison Linux (reclient)': {
        'chromium_config': 'chromium',
        'gclient_config': 'chromium',
        'chromium_apply_config': [
            'mb', 'goma_enable_cache_silo', 'goma_large_cache_file'
        ],
        'gclient_apply_config': ['reclient_test'],
        'platform': 'linux',
        'targets': ['all'],
    },
    'Comparison Mac (reclient)': {
        'chromium_config': 'chromium',
        'gclient_config': 'chromium',
        'chromium_apply_config': [
            'mb', 'goma_enable_cache_silo', 'goma_large_cache_file'
        ],
        'gclient_apply_config': ['reclient_test'],
        'platform': 'mac',
        'chromium_config_kwargs': {
            'TARGET_BITS': 64,
            'TARGET_PLATFORM': 'mac',
        },
        'simulation_platform': 'mac',
        'targets': ['all'],
    },
    'Comparison Mac arm64 (reclient)': {
        'chromium_config': 'chromium',
        'gclient_config': 'chromium',
        'chromium_apply_config': [
            'mb', 'goma_enable_cache_silo', 'goma_large_cache_file'
        ],
        'gclient_apply_config': ['reclient_test'],
        'platform': 'mac',
        'chromium_config_kwargs': {
            'TARGET_ARCH': 'arm',
            'TARGET_BITS': 64,
            'TARGET_PLATFORM': 'mac',
        },
        'simulation_platform': 'mac',
        'targets': ['all'],
    },
    'Comparison Mac arm64 on arm64 (reclient)': {
        'chromium_config': 'chromium',
        'gclient_config': 'chromium',
        'chromium_apply_config': [
            'mb', 'goma_enable_cache_silo', 'goma_large_cache_file'
        ],
        'gclient_apply_config': ['reclient_test'],
        'platform': 'mac',
        'chromium_config_kwargs': {
            'TARGET_ARCH': 'arm',
            'TARGET_BITS': 64,
            'TARGET_PLATFORM': 'mac',
        },
        'simulation_platform': 'mac',
        'targets': ['all'],
    },
    'Comparison Windows (reclient)': {
        'chromium_config': 'chromium',
        'gclient_config': 'chromium',
        'chromium_apply_config': [
            'mb', 'goma_enable_cache_silo', 'goma_large_cache_file'
        ],
        'gclient_apply_config': ['reclient_test'],
        'platform': 'win',
        'targets': ['all'],
    },
    'Comparison Windows (8 cores) (reclient)': {
        'chromium_config': 'chromium',
        'gclient_config': 'chromium',
        'chromium_apply_config': [
            'mb', 'goma_enable_cache_silo', 'goma_large_cache_file'
        ],
        'gclient_apply_config': ['reclient_test'],
        'platform': 'win',
        'targets': ['all'],
    },
    'Comparison Simple Chrome (reclient)': {
        'chromium_config': 'chromium',
        'gclient_config': 'chromium',
        'chromium_apply_config': [
            'mb', 'goma_enable_cache_silo', 'goma_large_cache_file'
        ],
        'gclient_apply_config': [
            'chromeos', 'reclient_test', 'checkout_lacros_sdk'
        ],
        'platform': 'linux',
        'chromium_config_kwargs': {
            'TARGET_BITS': 64,
            'TARGET_PLATFORM': 'chromeos',
            'CROS_BOARDS_WITH_QEMU_IMAGES': 'amd64-generic:amd64-generic-vm',
            'TARGET_CROS_BOARDS': 'amd64-generic',
        },
        'simulation_platform': 'linux',
        'targets': ['chrome'],
    },
    'Comparison ios (reclient)': {
        'chromium_config': 'chromium',
        'gclient_config': 'ios',
        'chromium_apply_config': [
            'mb', 'mac_toolchain', 'goma_enable_cache_silo',
            'goma_large_cache_file'
        ],
        'gclient_apply_config': ['reclient_test'],
        'platform': 'mac',
        'chromium_config_kwargs': {
            'TARGET_BITS': 64,
            'TARGET_PLATFORM': 'ios',
        },
        'simulation_platform': 'mac',
        'targets': ['all'],
    },
})


def ConfigureChromiumBuilder(api, recipe_config):
  api.chromium.set_config(
      recipe_config['chromium_config'],
      **recipe_config.get('chromium_config_kwargs',
                          {'BUILD_CONFIG': 'Release'}))
  api.gclient.set_config(recipe_config['gclient_config'],
                         **recipe_config.get('gclient_config_kwargs', {}))

  for c in recipe_config.get('chromium_apply_config', []):
    api.chromium.apply_config(c)

  for c in recipe_config.get('gclient_apply_config', []):
    api.gclient.apply_config(c)

  if api.chromium.c.CROS_BOARDS_WITH_QEMU_IMAGES:
    gclient_solution = api.gclient.c.solutions[0]
    gclient_solution.custom_vars['cros_boards_with_qemu_images'] = (
        api.chromium.c.CROS_BOARDS_WITH_QEMU_IMAGES)
  # Checkout chromium.
  api.chromium_checkout.ensure_checkout()


def GetConfig(buildername):
  # Match builders by prefix so that multiple builders
  # (CI,CQ,experimental) can share the same configs.
  for prefix, recipe_config in sorted(COMPARISON_BUILDERS.items()):
    if buildername.startswith(prefix):
      return prefix, recipe_config
  raise NotImplementedError('Unexpected builder %s' %
                            buildername)  #pragma: nocover


def RunSteps(api):
  # Use prefix as buildername so that the correct mb entry is used
  buildername, recipe_config = GetConfig(api.buildbucket.builder_name)

  # Set up a named cache so runhooks doesn't redownload everything on each run.
  solution_path = api.path['cache'].join('builder')
  api.file.ensure_directory('init cache if not exists', solution_path)

  with api.context(cwd=solution_path):
    ConfigureChromiumBuilder(api, recipe_config)

  base_out_dir = str(api.chromium.output_dir).rstrip('\\/')

  out_dirs = [base_out_dir] + [base_out_dir + '.' + ext for ext in '12']

  # Clear output directories for build
  clean_output_dirs(api, out_dirs)

  targets = recipe_config['targets']

  api.chromium.ensure_goma()
  api.chromium.ensure_toolchains()
  with api.context(cwd=solution_path):
    api.chromium.runhooks()
  try:
    # Do goma build .1 out directory
    target = '%s.1' % api.chromium.c.build_config_fs
    build_dir = '//out/%s' % target

    builder_id = chromium.BuilderId.create_for_group(
        api.builder_group.for_current, buildername)
    api.chromium.mb_gen(
        builder_id, build_dir=build_dir, phase='goma', recursive_lookup=True)

    raw_result = api.chromium.compile(
        targets, name='Goma build', use_goma_module=True, target=target)
    if raw_result.status != common_pb.SUCCESS:
      return raw_result

    # Clear output directories for build
    clean_output_dirs(api, out_dirs)

    # Do reclient build in .2 out directory
    target = '%s.2' % api.chromium.c.build_config_fs
    build_dir = '//out/%s' % target

    api.chromium.mb_gen(
        builder_id,
        build_dir=build_dir,
        phase='reclient',
        recursive_lookup=True)

    raw_result = api.chromium.compile(
        targets,
        name='Reclient build',
        use_goma_module=False,
        use_reclient=True,
        target=target)
    if raw_result.status != common_pb.SUCCESS:
      return raw_result
  finally:
    # Always clean output directories after build
    clean_output_dirs(api, out_dirs)


def clean_output_dirs(api, out_dirs):
  with api.step.nest('clean_output_dirs'):
    for out_dir in out_dirs:
      api.file.rmtree('rmtree %s' % out_dir, out_dir)


def _sanitize_nonalpha(text):
  return ''.join(c if c.isalnum() else '_' for c in text)


def GenTests(api):
  builder_group = 'chromium.swarm'
  for buildername in COMPARISON_BUILDERS:
    test_name = 'full_%s_%s' % (_sanitize_nonalpha(builder_group),
                                _sanitize_nonalpha(buildername))
    mac_test_props = {
        'xcode_build_version': '12345',
        'configs': ['mac_toolchain']
    } if COMPARISON_BUILDERS[buildername]['platform'] == 'mac' else {}
    yield api.test(
        test_name,
        api.chromium.ci_build(builder_group=builder_group, builder=buildername),
        api.reclient.properties(),
        api.platform(COMPARISON_BUILDERS[buildername]['platform'], 64),
        api.properties(
            buildername=buildername,
            buildnumber=571,
            configuration='Release',
            **mac_test_props),
        api.post_process(post_process.DropExpectation),
    )

  yield api.test(
      'first_build_compile_fail',
      api.chromium.ci_build(
          builder_group=builder_group, builder='Comparison Linux (reclient)'),
      api.reclient.properties(),
      api.properties(
          buildername='Comparison Linux (reclient)', buildnumber=571),
      api.platform(
          COMPARISON_BUILDERS['Comparison Linux (reclient)']['platform'], 64),
      api.properties(configuration='Release'),
      api.step_data('Goma build', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'second_build_compile_fail',
      api.chromium.ci_build(
          builder_group=builder_group, builder='Comparison Linux (reclient)'),
      api.reclient.properties(),
      api.properties(
          buildername='Comparison Linux (reclient)', buildnumber=571),
      api.platform(
          COMPARISON_BUILDERS['Comparison Linux (reclient)']['platform'], 64),
      api.properties(configuration='Release'),
      api.step_data('Reclient build', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
