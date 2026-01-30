# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from recipe_engine.engine_types import freeze
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

DEPS = [
    'adb',
    'chromium',
    'chromium_android',
    'depot_tools/bot_update',
    'depot_tools/gclient',
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
    'no_strict_mode_tester': {
        'strict_mode': 'off',
    },
    'device_flags_builder': {
        'device_flags': 'device_flags_file',
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
    'stackwalker': {
        'run_stackwalker': True,
    },
})


def RunSteps(api):
  config = BUILDERS[api.buildbucket.builder_name]

  api.chromium.set_config(
      'base_config',
      BUILD_CONFIG='Release',
      TARGET_ARCH='arm',
      TARGET_BITS=32,
      TARGET_PLATFORM='android')

  api.chromium_android.set_config('base_config', INTERNAL=True)
  api.chromium_android.c.logcat_bucket = None

  for c in config.get('android_apply_config', []):
    api.chromium_android.apply_config(c)

  gclient_cfg = api.gclient.make_config('chromium_bare')
  gclient_cfg.target_os = ['android']

  update_result = api.bot_update.ensure_checkout(gclient_cfg)
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)

  api.chromium_android.clean_local_files(source_dir)

  api.chromium.runhooks(source_dir, build_dir)
  api.chromium_android.run_tree_truth(update_result, additional_repos=['foo'])
  assert 'MAJOR' in api.chromium.get_version(source_dir)

  if config.get('build', False):
    raw_result = api.chromium.compile(source_dir, build_dir)
    if raw_result.status != common_pb.SUCCESS:
      return raw_result
    api.chromium_android.make_zip_archive(
        'zip_build_product',
        'archive.zip',
        build_dir,
        include_filters=['*.apk'],
        exclude_filters=['*.so', '*.a'])
  else:
    api.chromium_android.download_build(source_dir, 'build-bucket',
                                        'build_product.zip')

  api.adb.root_devices(api.chromium_android.adb_path(source_dir))
  api.chromium_android.spawn_logcat_monitor(source_dir)

  failure = False
  try:
    api.chromium_android.provision_devices(
        source_dir,
        skip_wipe=config.get('skip_wipe', False),
        disable_location=config.get('disable_location', False),
        reboot_timeout=1800)

    api.chromium_android.common_tests_setup_steps(source_dir, skip_wipe=True)

  except api.step.StepFailure as f:
    failure = f

  api.chromium_android.run_test_suite(
      source_dir,
      build_dir,
      'unittests',
      result_details=config.get('result_details'),
      store_tombstones=config.get('store_tombstones'))

  api.chromium_android.logcat_dump(source_dir, build_dir)
  api.chromium_android.stack_tool_steps(source_dir)

  if config.get('run_stackwalker'):
    chrome_breakpad_binary = build_dir / 'lib.unstripped/libchrome.so'
    webview_breakpad_binary = build_dir / 'lib.unstripped/libwebviewchromium.so'
    dump_syms_binary = build_dir / 'dump_syms'
    microdump_stackwalk_binary = build_dir / 'microdump_stackwalk'
    api.path.mock_add_paths(chrome_breakpad_binary)
    api.path.mock_add_paths(webview_breakpad_binary)
    api.path.mock_add_paths(dump_syms_binary)
    api.path.mock_add_paths(microdump_stackwalk_binary)

    api.chromium_android.common_tests_final_steps(
        source_dir, build_dir, run_stackwalker=True)


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
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'tester_no_devices_during_status',
      properties_for('tester'),
      api.step_data('device_status', retcode=1),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'tester_other_device_failure_during_recovery',
      properties_for('tester'),
      api.step_data('device_recovery', retcode=2),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'tester_other_device_failure_during_status',
      properties_for('tester'),
      api.step_data('device_status', retcode=2),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'tester_with_step_warning',
      properties_for('tester'),
      api.step_data('unittests', retcode=88),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'handle_error_exit_code',
      properties_for('tester'),
      api.step_data('provision_devices', retcode=1),
      api.expect_status('INFRA_FAILURE'),
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
