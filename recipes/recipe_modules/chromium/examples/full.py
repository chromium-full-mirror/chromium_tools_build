# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'reclient',
    'siso',
]

def RunSteps(api):
  use_goma_module = api.properties.get('use_goma_module', False)
  use_reclient = api.properties.get('use_reclient', False)
  out_dir = api.properties.get('out_dir', None)

  configs = api.properties.get('configs', [])
  assert api.chromium.build_properties == None

  with api.chromium.chromium_layout():
    builder_id, builder_config = (
        api.chromium_tests_builder_config.lookup_builder())
    api.chromium_tests.configure_build(builder_config)

    api.chromium.get_build_target_arch()

    for config in configs:
      api.chromium.apply_config(config)

    api.chromium_tests.prepare_checkout(builder_config, set_output_commit=False)

    mb_config_path = api.properties.get('mb_config_path')

    api.chromium.mb_gen(
        builder_id,
        use_goma=True,
        mb_config_path=mb_config_path,
        android_version_code=3,
        android_version_name="example")

    return api.chromium.compile(
        targets=['All'],
        out_dir=out_dir,
        use_goma_module=use_goma_module,
        use_reclient=use_reclient)


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  yield api.test(
      'basic_out_dir',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(out_dir='/tmp'),
  )

  yield api.test(
      'basic_out_dir_with_custom_mb_config',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          out_dir='/tmp',
          mb_config_path='/custom/config.pyl',
      ),
  )

  yield api.test(
      'basic_out_dir_with_goma_module',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          use_goma_module=True,
          out_dir='/tmp',
      ),
  )

  yield api.test(
      'basic_out_dir_with_reclient',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          use_reclient=True,
          out_dir='/tmp',
      ),
      api.reclient.properties(),
      api.post_check(lambda check, steps: check({
          'RBE_server_address', 'RBE_log_dir'
      }.issubset(steps['compile'].env))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_out_dir_with_siso',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          use_reclient=True,
          out_dir='/tmp',
      ),
      api.reclient.properties(),
      api.siso.properties(),
      api.post_check(
          lambda check, steps: check({'RBE_server_address', 'RBE_log_dir'}.
                                     issubset(steps['compile'].env))),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_no_out_dir_with_goma_module',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(use_goma_module=True),
  )

  yield api.test(
      'basic_out_dir_goma_module_build_failure',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          out_dir='/tmp',
          use_goma_module=True,
      ),
      api.step_data('compile', retcode=1),
      api.step_data(
          'postprocess_for_goma.goma_jsonstatus',
          api.json.output(
              data={
                  'notice': [{
                      'infra_status': {
                          'ping_status_code': 200,
                          'num_user_error': 1,
                      },
                  },],
              })),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'basic_out_dir_ninja_build_failure',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          out_dir='/tmp',
      ),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'basic_out_dir_ninja_no_op_failure',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          out_dir='/tmp',
          use_goma_module=True,
      ),
      api.override_step_data(
          'compile confirm no-op',
          stdout=api.raw_io.output_text("ninja explain: chrome is dirty\n")),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'basic_out_dir_goma_module_start_failure',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          out_dir='/tmp',
          use_goma_module=True,
      ),
      api.step_data('preprocess_for_goma.start_goma', retcode=1),
      api.step_data(
          'preprocess_for_goma.goma_jsonstatus',
          api.json.output(data={
              'notice': [{
                  "compile_error": "COMPILER_PROXY_UNREACHABLE",
              },],
          })),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'basic_out_dir_goma_module_ping_failure',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          out_dir='/tmp',
          use_goma_module=True,
      ),
      api.step_data('preprocess_for_goma.start_goma', retcode=1),
      api.step_data(
          'preprocess_for_goma.goma_jsonstatus',
          api.json.output(data={
              'notice': [{
                  'infra_status': {
                      'ping_status_code': 408,
                  },
              },],
          })),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'mac_basic',
      api.platform('mac', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          out_dir='/tmp',
          target_platform='mac',
      ),
  )

  yield api.test(
      'mac_toolchain',
      api.platform('mac', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          out_dir='/tmp',
          target_platform='mac',
          configs=['mac_toolchain'],
      ),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'mac_toolchain_properties',
      api.platform('mac', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          out_dir='/tmp',
          target_platform='mac',
          configs=['mac_toolchain'],
          xcode_build_version='12345',
      ),
  )

  yield api.test(
      'chromeos_simplechrome',
      api.platform('linux', 64),
      api.chromium.ci_build(
          builder_group='chromium.chromiumos',
          builder='chromeos-amd64-generic-rel',
          bot_id='build1-a1',
          build_number=77457,
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='chromium.chromiumos',
              builder='chromeos-amd64-generic-rel',
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  gclient_apply_config=['chromeos'],
                  chromium_config='chromium',
                  chromium_apply_config=['mb'],
                  chromium_config_kwargs={
                      'BUILD_CONFIG': 'Release',
                      'TARGET_ARCH': 'intel',
                      'TARGET_BITS': 64,
                      'TARGET_PLATFORM': 'chromeos',
                      'CROS_BOARDS_WITH_QEMU_IMAGES': 'amd64-generic-vm',
                  },
                  build_gs_bucket='chromium-chromiumos-archive',
              ),
          ).assemble()),
      api.properties(out_dir='/tmp'),
  )
