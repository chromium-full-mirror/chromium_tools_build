# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.engine_types import freeze
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

DEPS = [
    'adb',
    'build',
    'chromium',
    'chromium_android',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
]

BUILDERS = freeze({
    'basic_builder': {
        'target': 'Release',
        'build': True,
    },
    'tester': {},
    'gerrit_try_builder': {
        'build': True,
        'skip_wipe': True,
    },
    'webview_tester': {
        'android_apply_config': ['remove_all_system_webviews'],
    },
    'slow_tester': {
        'timeout_scale': 2,
    },
    'downgrade_install_tester': {
        'specific_install': True,
        'downgrade': True,
    },
    'keep_data_install_tester': {
        'specific_install': True,
        'keep_data': True,
    },
    'no_strict_mode_tester': {
        'strict_mode': 'off',
    },
    'device_flags_builder': {
        'device_flags': 'device_flags_file',
    },
    'no_cache_builder': {
        'use_git_cache': False,
    },
    'json_results_file': {
        'json_results_file': 'json_results_file',
    },
    'result_details': {
        'result_details': True,
        'store_tombstones': True,
    },
    'disable_location_tester': {
        'disable_location': True,
    },
    'use_devil_adb': {
      'android_apply_config': ['use_devil_adb'],
    },
    'remove_system_vrcore': {
      'android_apply_config': ['remove_system_vrcore'],
    },
    'stackwalker': {
      'run_stackwalker': True,
    },
})


def RunSteps(api):
  config = BUILDERS[api.buildbucket.builder_name]

  api.chromium_android.configure_from_properties(
      'base_config',
      REPO_URL='svn://svn.chromium.org/chrome/trunk/src',
      REPO_NAME='src/repo',
      INTERNAL=True,
      BUILD_CONFIG='Release')

  api.chromium_android.c.get_app_manifest_vars = True
  api.chromium_android.c.logcat_bucket = None

  for c in config.get('android_apply_config', []):
    api.chromium_android.apply_config(c)

  api.chromium_android.init_and_sync(
      use_bot_update=False, use_git_cache=config.get('use_git_cache', True))

  if config.get('build', False):
    api.chromium.ensure_goma()
  api.chromium.runhooks()
  api.chromium_android.run_tree_truth(additional_repos=['foo'])
  assert 'MAJOR' in api.chromium.get_version()

  api.chromium_android.host_info()

  if config.get('build', False):
    raw_result = api.chromium.compile(use_goma_module=True)
    if raw_result.status != common_pb.SUCCESS:
      return raw_result
    api.chromium_android.make_zip_archive(
        'zip_build_product', 'archive.zip', include_filters=['*.apk'],
        exclude_filters=['*.so', '*.a'])
  else:
    api.chromium_android.download_build('build-bucket',
                                        'build_product.zip')

  if config.get('specific_install'):
    api.chromium_android.adb_install_apk(
        'Chrome.apk',
        devices=['abc123'],
        allow_downgrade=config.get('downgrade', False),
        keep_data=config.get('keep_data', False),
    )

  api.adb.root_devices()
  api.chromium_android.spawn_logcat_monitor()

  failure = False
  try:
    # TODO(luqui): remove redundant cruft, need one consistent API.
    api.chromium_android.device_status_check()

    api.path.mock_add_paths(api.chromium_android.known_devices_file)
    api.chromium_android.device_status_check()

    api.chromium_android.provision_devices(
        skip_wipe=config.get('skip_wipe', False),
        disable_location=config.get('disable_location', False),
        reboot_timeout=1800)

    api.chromium_android.common_tests_setup_steps(skip_wipe=True)

  except api.step.StepFailure as f:
    failure = f

  api.chromium_android.monkey_test()

  api.chromium_android.run_test_suite(
      'unittests',
      result_details=config.get('result_details'),
      store_tombstones=config.get('store_tombstones'))
  if not failure:
    api.chromium_android.run_bisect_script(
        extra_src='test.py', path_to_config='test.py')

  api.chromium_android.logcat_dump()
  api.chromium_android.stack_tool_steps()

  if config.get('run_stackwalker'):
    chrome_breakpad_binary = api.path['checkout'].join(
        'out', api.chromium.c.BUILD_CONFIG, 'lib.unstripped', 'libchrome.so')
    webview_breakpad_binary = api.path['checkout'].join(
        'out', api.chromium.c.BUILD_CONFIG, 'lib.unstripped',
        'libwebviewchromium.so')
    dump_syms_binary = api.path['checkout'].join(
        'out', api.chromium.c.BUILD_CONFIG, 'dump_syms')
    microdump_stackwalk_binary = api.path['checkout'].join(
        'out', api.chromium.c.BUILD_CONFIG, 'microdump_stackwalk')
    api.path.mock_add_paths(chrome_breakpad_binary)
    api.path.mock_add_paths(webview_breakpad_binary)
    api.path.mock_add_paths(dump_syms_binary)
    api.path.mock_add_paths(microdump_stackwalk_binary)

    api.chromium_android.common_tests_final_steps(
        checkout_dir=api.path['checkout'])


  if failure:
    # pylint: disable=raising-bad-type
    raise failure

def GenTests(api):
  def properties_for(buildername):
    return sum([
        api.chromium.ci_build(
            builder=buildername,
            builder_group='chromium.android',
            revision='4f4b02f6b7fa20a3a25682c457bbc8ad589c8a00',
        ),
        api.properties(internal=True),
    ], api.empty_test_data())

  for buildername in BUILDERS:
    yield api.test(
        '%s_basic' % buildername,
        properties_for(buildername),
    )

  yield api.test(
      'tester_no_devices_during_recovery',
      properties_for('tester'),
      api.step_data('device_recovery', retcode=1),
  )

  yield api.test(
      'tester_no_devices_during_status',
      properties_for('tester'),
      api.step_data('device_status', retcode=1),
  )

  yield api.test(
      'tester_other_device_failure_during_recovery',
      properties_for('tester'),
      api.step_data('device_recovery', retcode=2),
  )

  yield api.test(
      'tester_other_device_failure_during_status',
      properties_for('tester'),
      api.step_data('device_status', retcode=2),
  )

  yield api.test(
      'tester_with_step_warning',
      properties_for('tester'),
      api.step_data('unittests', retcode=88),
  )

  yield api.test(
      'tester_failing_host_info',
      properties_for('tester'),
      api.step_data(
          'Host Info', api.json.output({
              'failures': ['foo', 'bar']
          }), retcode=1),
  )

  yield api.test(
      'tester_denylisted_devices',
      properties_for('tester'),
      api.override_step_data('provision_devices',
                             api.json.output(['abc123', 'def456'])),
  )

  yield api.test(
      'tester_offline_devices',
      properties_for('tester'),
      api.override_step_data('device_status', api.json.output([{}, {}])),
  )

  yield api.test(
      'gerrit_refs',
      api.chromium.try_build(
          builder_group='tryserver.chromium.android',
          builder='gerrit_try_builder',
          change_number=123456789,
          patch_set=1),
      api.properties(
          internal=True, **({
              'event.patchSet.ref': 'refs/changes/50/176150/1'
          })),
  )

  yield api.test(
      'tombstones_m53',
      properties_for('tester'),
      api.chromium.override_version(major=53),
  )

  yield api.test(
      'upload_result_details_failures',
      properties_for('result_details'),
      api.override_step_data('unittests: generate result details', retcode=1),
  )

  yield api.test(
      'compile_failure',
      properties_for('basic_builder'),
      api.step_data('compile', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
