# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import collections

from recipe_engine import post_process
from recipe_engine.post_process import Filter

from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests_builder_config import builder_spec

DEPS = [
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'reclient',
]

BUILDERS = ctbc.BuilderDatabase.create({
    'fake.group': {
        'Test Version':
            ctbc.BuilderSpec.create(
                android_config='main_builder_mb',
                chromium_config='chromium',
                gclient_config='chromium',
                android_version='chrome/Version',
            ),
        'Cronet':
            ctbc.BuilderSpec.create(
                chromium_config='android',
                chromium_apply_config=['cronet_builder'],
                gclient_apply_config=['android'],
                gclient_config='chromium',
                chromium_config_kwargs={
                    'BUILD_CONFIG': 'Release',
                    'TARGET_BITS': 32,
                    'TARGET_PLATFORM': 'android',
                },
                android_config='main_builder',
                execution_mode=builder_spec.COMPILE_AND_TEST,
                simulation_platform='linux',
            )
    }
})


def RunSteps(api):
  # Create a nested step so that setup steps can be easily filtered out
  with api.step.nest('setup steps'):
    builder_id, builder_config = (
        api.chromium_tests_builder_config.lookup_builder())
    api.chromium_tests.configure_build(builder_config)
    update_step, targets_config = (
        api.chromium_tests.prepare_checkout(builder_config))

  tests = []
  if api.properties.get('swarming_gtest'):
    tests.append(
        steps.SwarmingGTestTestSpec.create('base_unittests').get_test(
            api.chromium_tests))

  return api.chromium_tests.compile_specific_targets(
      builder_id,
      builder_config,
      update_step,
      targets_config,
      compile_targets=['base_unittests'],
      tests=tests,
      override_execution_mode=ctbc.COMPILE_AND_TEST)[0]


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  def filter_out_setup_steps():

    def step_filter(check, step_odict):
      del check
      return collections.OrderedDict([(k, v)
                                      for k, v in step_odict.items()
                                      if not k.startswith('setup steps')])

    return api.post_process(step_filter)

  yield api.test(
      'linux_tests',
      api.platform('linux', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-tester',
          parent_buildername='fake-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_tester(
              builder_group='fake-group',
              builder='fake-tester',
          ).with_parent(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarming_gtest=True),
      filter_out_setup_steps(),
  )

  yield api.test(
      'linux_tests_reclient',
      api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.reclient.properties(),
      api.properties(swarming_gtest=True),
      api.step_data('lookup GN args',
                    api.raw_io.stream_output_text('use_remoteexec = true\n')),
      # Check that we do use reclient as the distributed compiler
      api.post_process(post_process.MustRun,
                       'preprocess for reclient.start reproxy via bootstrap'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failure',
      api.platform('linux', 64),
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-tester',
          parent_buildername='fake-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_tester(
              builder_group='fake-group',
              builder='fake-tester',
          ).with_parent(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarming_gtest=True),
      api.step_data('compile', retcode=1),
      filter_out_setup_steps(),
  )

  yield api.test(
      'failure_tryserver',
      api.platform('linux', 64),
      api.chromium.try_build(
          builder_group='fake-try-group', builder='fake-try-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.step_data('compile (with patch)', retcode=1),
      filter_out_setup_steps(),
  )

  yield api.test(
      'perf_isolate_lookup',
      # pinpoint/builder.py does its own mapping of try builder to CI builder
      # because it wants a simple mapping that pulls in all triggered try
      # builders, which doesn't match the semantics of trybot/TrySpec
      api.chromium.try_build(
          builder_group='chromium.perf', builder='linux-builder-perf'),
      api.properties(
          deps_revision_overrides={'src': '12345678' * 5}, swarming_gtest=True),
      api.post_process(Filter('pinpoint isolate upload')),
  )

  yield api.test(
      'perf_isolate_lookup_v8',
      # pinpoint/builder.py does its own mapping of try builder to CI builder
      # because it wants a simple mapping that pulls in all triggered try
      # builders, which doesn't match the semantics of trybot/TrySpec
      api.chromium.try_build(
          builder='linux-builder-perf',
          builder_group='chromium.perf',
          git_repo='https://chromium.googlesource.com/v8/v8',
          project='v8/v8',
          revision='1234abcd' * 5),
      api.properties(
          deps_revision_overrides={'v8': '1234abcd' * 5}, swarming_gtest=True),
      api.post_process(Filter('pinpoint isolate upload')),
  )

  yield api.test(
      'perf_isolate_lookup_no_commit',
      # pinpoint/builder.py does its own mapping of try builder to CI builder
      # because it wants a simple mapping that pulls in all triggered try
      # builders, which doesn't match the semantics of trybot/TrySpec
      api.chromium.try_build(
          builder='linux-builder-perf',
          builder_group='chromium.perf',
          git_repo=None,
          revision=None),
      api.properties(swarming_gtest=True),
      api.post_process(Filter('pinpoint isolate upload')),
  )

  yield api.test(
      'android',
      api.platform.arch('arm'),
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake.group', builder='Cronet', builder_db=BUILDERS),
      filter_out_setup_steps(),
  )

  yield api.test(
      'android_version',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake.group',
          builder='Test Version',
          builder_db=BUILDERS),
      api.chromium.override_version(major=123, minor=1, build=9876, patch=2),
      filter_out_setup_steps(),
  )
