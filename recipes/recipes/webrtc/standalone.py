# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# Recipe for building and running tests for WebRTC stand-alone.

import functools

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from RECIPE_MODULES.build import chromium_types
from RECIPE_MODULES.build.webrtc import builders

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
  gn,
  webrtc,
)
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  led,
  platform,
  raw_io,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  gn: gn.API
  led: led.API
  platform: platform.API
  raw_io: raw_io.API
  step: step.API
  tryserver: tryserver.API
  webrtc: webrtc.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  raw_io: raw_io.TEST_API
  tryserver: tryserver.TEST_API
  webrtc: webrtc.TEST_API


def RunSteps(api: DEPS):
  builder_id, builder_config = api.chromium_tests_builder_config.lookup_builder(
    builder_db=builders.BUILDERS_DB
  )
  api.webrtc.apply_bot_config(builder_id, builder_config)

  update_result = api.chromium_checkout.ensure_checkout()
  checkout_dir = update_result.checkout_dir
  source_dir = update_result.source_root.path

  for phase in builders.BUILDERS_DB[builder_id].phases:
    build_dir = api.chromium.default_build_dir(source_dir)

    targets_config = api.chromium_tests.create_targets_config(
      builder_config,
      update_result.properties,
      source_dir,
      build_dir,
      checkout_dir=checkout_dir,
    )

    api.chromium_swarming.configure_swarming(
      precommit=api.tryserver.is_tryserver
    )

    if api.webrtc.should_download_video_quality_tools(
      builder_id, builder_config
    ):
      api.webrtc.download_video_quality_tools(source_dir)

    if api.webrtc.should_generate_code_coverage(builder_id, builder_config):
      api.webrtc.setup_code_coverage_module(source_dir, build_dir)

    api.chromium.ensure_toolchains(update_result.checkout_dir)
    api.chromium.runhooks(source_dir, build_dir)
    test_targets, compile_targets = api.webrtc.determine_compilation_targets(
      source_dir, build_dir, builder_id, targets_config, phase
    )
    if not compile_targets:
      step_result = api.step('No further steps are necessary.', cmd=None)
      step_result.presentation.status = api.step.SUCCESS
      continue

    tests_to_compile = [
      t for t in targets_config.all_tests if t.target_name in test_targets
    ]

    with api.chromium.guard_compile(build_dir):
      api.webrtc.run_mb(
        source_dir, build_dir, builder_id, phase, tests_to_compile
      )
      raw_result = api.chromium.compile(
        source_dir, build_dir, targets=compile_targets
      )
      if raw_result.status != common_pb.SUCCESS:
        return raw_result

    api.webrtc.isolate(build_dir, builder_id, tests_to_compile)

    builder_spec = builders.BUILDERS_DB[builder_id]
    if builder_spec.binary_size_files:
      api.webrtc.get_binary_sizes(builder_spec.binary_size_files, build_dir)
    if not api.led.launched_by_led and builder_spec.build_android_archive:
      api.webrtc.build_android_archive(source_dir)
    if not api.led.launched_by_led and builder_spec.archive_apprtc:
      api.webrtc.package_apprtcmobile(build_dir, builder_id)
    if builder_spec.include_cleaner:
      return api.webrtc.include_cleaner(source_dir, build_dir)

    tests_to_run = [
      t
      for t in targets_config.tests_on(builder_id)
      if t.target_name in test_targets
    ]

    test_failure_summary = api.webrtc.run_tests(
      checkout_dir,
      source_dir,
      build_dir,
      builder_id,
      tests_to_run,
      retry_invalid_shards=True,
    )

    if test_failure_summary:
      return test_failure_summary

    api.webrtc.trigger_child_builds(
      builder_id, builder_config, source_dir, build_dir, update_result
    )


def GenTests(api: TEST_DEPS):
  builders_db = builders.BUILDERS_DB
  generate_builder = functools.partial(api.webrtc.generate_builder, builders_db)

  for builder_id in builders_db:
    yield generate_builder(builder_id)

  builder_id = chromium_types.BuilderId.create_for_group(
    'client.webrtc', 'Linux64 Debug'
  )
  yield generate_builder(builder_id, failing_test=True, suffix='_failing_test')
  yield generate_builder(
    builder_id,
    tags=[{'key': 'pinpoint_job_id', 'value': ''}],
    suffix='_pinpoint',
  )
  yield generate_builder(builder_id, suffix='_fail_compile', fail_compile=True)

  builder_id = chromium_types.BuilderId.create_for_group(
    'client.webrtc', 'Android64'
  )
  yield generate_builder(
    builder_id, fail_android_archive=True, suffix='_failing_archive'
  )

  builder_id = chromium_types.BuilderId.create_for_group(
    'tryserver.webrtc', 'iwyu_verifier'
  )
  yield generate_builder(builder_id, fail_iwyu=True, suffix='_failing_iwyu')
  yield generate_builder(builder_id, suffix='_skip_file') + api.step_data(
    'git diff to analyze patch (2)', api.raw_io.stream_output('skip.cc')
  )
  yield generate_builder(
    builder_id, suffix='_skipping_iwyu'
  ) + api.tryserver.get_footers({'No-Iwyu': ['skip']})

  builder_id = chromium_types.BuilderId.create_for_group(
    'client.webrtc.perf', 'Perf Linux'
  )
  yield generate_builder(
    builder_id, is_experimental=True, suffix='_experimental'
  )

  builder_id = chromium_types.BuilderId.create_for_group(
    'tryserver.webrtc', 'linux_compile_arm_rel'
  )
  gn_analyze_no_deps_output = {'status': ['No dependency']}
  yield generate_builder(
    builder_id,
    suffix='_gn_analyze_no_dependency',
    gn_analyze_output=gn_analyze_no_deps_output,
  )
