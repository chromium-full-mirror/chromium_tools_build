# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Recipe for building and running tests for WebRTC stand-alone.

import functools

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.build.webrtc import builders

DEPS = [
    'chromium',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'chromium_swarming',
    'depot_tools/tryserver',
    'gn',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/step',
    'webrtc',
]


def RunSteps(api):
  builder_id, builder_config = api.chromium_tests_builder_config.lookup_builder(
      builder_db=builders.BUILDERS_DB)
  api.webrtc.apply_bot_config(builder_id, builder_config)

  update_step = api.chromium_checkout.ensure_checkout()
  targets_config = api.chromium_tests.create_targets_config(
      builder_config, update_step.presentation.properties, api.path['checkout'])

  api.chromium_swarming.configure_swarming(
      'webrtc', precommit=api.tryserver.is_tryserver)

  if api.webrtc.should_download_video_quality_tools(builder_id, builder_config):
    api.webrtc.download_video_quality_tools()

  if api.webrtc.should_generate_code_coverage(builder_id, builder_config):
    api.webrtc.setup_code_coverage_module()

  api.chromium.ensure_toolchains()
  api.chromium.runhooks()

  for phase in builders.BUILDERS_DB[builder_id].phases:
    test_targets, compile_targets = api.webrtc.determine_compilation_targets(
        builder_id, targets_config, phase)
    if not compile_targets:
      step_result = api.step('No further steps are necessary.', cmd=None)
      step_result.presentation.status = api.step.SUCCESS
      return

    tests_to_compile = [
        t for t in targets_config.all_tests if t.target_name in test_targets
    ]

    api.webrtc.run_mb(builder_id, phase, tests_to_compile)
    raw_result = api.chromium.compile(compile_targets, use_reclient=True)
    if raw_result.status != common_pb.SUCCESS:
      return raw_result
    api.webrtc.isolate(builder_id, builder_config, tests_to_compile)

    builder_spec = builders.BUILDERS_DB[builder_id]
    if builder_spec.binary_size_files:
      api.webrtc.get_binary_sizes(builder_spec.binary_size_files)
    if builder_spec.build_android_archive:
      api.webrtc.build_android_archive()
    if builder_spec.archive_apprtc:
      api.webrtc.package_apprtcmobile(builder_id)

    tests_to_run = [
        t for t in targets_config.tests_on(builder_id)
        if t.target_name in test_targets
    ]

    test_failure_summary = api.webrtc.run_tests(builder_id, tests_to_run)

    if test_failure_summary:
      return test_failure_summary

  api.webrtc.trigger_child_builds(builder_id, builder_config, update_step)


def GenTests(api):
  builders_db = builders.BUILDERS_DB
  generate_builder = functools.partial(api.webrtc.generate_builder, builders_db)

  for builder_id in builders_db:
    yield generate_builder(builder_id)

  builder_id = chromium.BuilderId.create_for_group('client.webrtc',
                                                   'Linux64 Debug')
  yield generate_builder(builder_id, failing_test=True, suffix='_failing_test')
  yield generate_builder(
      builder_id,
      tags=[{
          'key': 'pinpoint_job_id',
          'value': ''
      }],
      suffix='_pinpoint')
  yield generate_builder(builder_id, suffix='_fail_compile', fail_compile=True)

  builder_id = chromium.BuilderId.create_for_group('client.webrtc', 'Android32')
  yield generate_builder(
      builder_id,
      fail_android_archive=True,
      suffix='_failing_archive')

  builder_id = chromium.BuilderId.create_for_group('client.webrtc.perf',
                                                   'Perf Linux Bionic')
  yield generate_builder(
      builder_id, is_experimental=True, suffix='_experimental')

  builder_id = chromium.BuilderId.create_for_group('tryserver.webrtc',
                                                   'linux_compile_arm_rel')
  gn_analyze_no_deps_output = {'status': ['No dependency']}
  yield generate_builder(
      builder_id,
      suffix='_gn_analyze_no_dependency',
      gn_analyze_output=gn_analyze_no_deps_output)
