# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import collections

from recipe_engine import post_process
from recipe_engine.post_process import Filter

from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec
from RECIPE_MODULES.build.chromium_tests_builder_config import builder_spec

DEPS = [
    'chromium',
    'chromium_rts',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/bot_update',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'siso',
]

BUILDERS = ctbc.BuilderDatabase.create({
    'fake.group': {
        'Test Version':
            ctbc.BuilderSpec.create(
                android_config='base_config',
                chromium_config='main_builder',
                chromium_apply_config=['mb'],
                chromium_config_kwargs={
                    'BUILD_CONFIG': 'Debug',
                    'TARGET_ARCH': 'arm',
                    'TARGET_BITS': 32,
                    'TARGET_PLATFORM': 'android',
                },
                gclient_config='chromium',
                android_version='chrome/Version',
            ),
        'Cronet':
            ctbc.BuilderSpec.create(
                chromium_config='main_builder',
                chromium_apply_config=['cronet_builder'],
                gclient_apply_config=['android'],
                gclient_config='chromium',
                chromium_config_kwargs={
                    'BUILD_CONFIG': 'Release',
                    'TARGET_ARCH': 'arm',
                    'TARGET_BITS': 32,
                    'TARGET_PLATFORM': 'android',
                },
                android_config='base_config',
                execution_mode=builder_spec.COMPILE_AND_TEST,
                simulation_platform='linux',
            ),
        'Android Tester CAS':
            ctbc.BuilderSpec.create(
                chromium_config='main_builder',
                gclient_config='chromium',
                chromium_config_kwargs={
                    'BUILD_CONFIG': 'Release',
                    'TARGET_ARCH': 'arm',
                    'TARGET_BITS': 32,
                    'TARGET_PLATFORM': 'android',
                },
                android_config='base_config',
                execution_mode=builder_spec.TEST,
                parent_buildername='Cronet',
                use_test_trigger_cas=True,
            ),
        'chromium-rel':
            ctbc.BuilderSpec.create(
                chromium_config='chromium',
                gclient_config='chromium',
            )
    }
})

_TEST_TRYBOTS = ctbc.TryDatabase.create({
    'tryserver.chromium.test': {
        'rts-rel':
            ctbc.TrySpec.create(
                mirrors=[
                    ctbc.TryMirror.create(
                        builder_group='fake.group',
                        buildername='chromium-rel',
                        tester='chromium-rel',
                    ),
                ],),
    }
})

def RunSteps(api):
  # Create a nested step so that setup steps can be easily filtered out
  with api.step.nest('setup steps'):
    builder_id, builder_config = (
        api.chromium_tests_builder_config.lookup_builder())
    api.chromium_tests.configure_build(builder_config)
    update_result, build_dir, targets_config = (
        api.chromium_tests.prepare_checkout(builder_config))

  tests = []
  target_name = 'base_unittests'
  if api.properties.get('swarming_gtest'):
    target_name = api.properties.get('test_target_name', 'base_unittests')
    tests.append(
        steps.SwarmingGTestTestSpec.create(target_name).get_test(
            api.chromium_tests))
  return api.chromium_tests.compile_specific_targets(
      build_dir,
      builder_id,
      builder_config,
      update_result,
      targets_config,
      compile_targets=[target_name],
      tests=tests,
      override_execution_mode=ctbc.COMPILE_AND_TEST)[0]


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

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
      api.post_process(post_process.StepCommandContains, 'lookup GN args', [
          '-m',
          'fake-group',
          '-b',
          'fake-tester',
      ]),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux_tests_ssci_experimental',
      api.chromium.ci_build(
          builder_group='fake-group',
          builder='fake-builder',
          experiments=['ssci.experimental']),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.siso.properties(),
      api.properties(
          swarming_gtest=True,
          **{'$build/ssci': {
              "targets": ["//example:example"],
          }}),
      api.override_step_data(
          'SSCI collection.run depbot',
          api.json.output(
              name="summary",
              data={
                  "targets": [{
                      "entry_point": "//example:example",
                      "target": "//example:example",
                      "artifacts_file_path": "out/Release/artifacts.json",
                      "libraries_file_path": "out/Release/libs.json"
                  }],
              })),
      api.post_process(post_process.MustRun, 'SSCI collection.run depbot'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux_tests_no_ssci_experimental',
      api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.siso.properties(),
      api.properties(swarming_gtest=True),
      api.post_process(post_process.DoesNotRun, 'SSCI collection.run depbot'),
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
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.SummaryMarkdownRE,
          '#### Step _compile_ failed. Error logs are shown below:'),
      api.post_process(post_process.DropExpectation),
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
      api.expect_status('FAILURE'),
      api.post_process(
          post_process.SummaryMarkdownRE,
          r'#### Step _compile \(with patch\)_ failed\. ' +
          'Error logs are shown below:'),
      api.post_process(post_process.DropExpectation),
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
      # The important bit here is the presence of the two "git_hash" entries.
      api.
      post_process(post_process.StepCommandContains, 'pinpoint isolate upload', [
          '{"builder_name": "linux-builder-perf", '
          '"change": "{\\"commits\\": ['
          '{\\"git_hash\\": \\"1234567812345678123456781234567812345678\\", '
          '\\"repository\\": \\"chromium\\"}, '
          '{\\"git_hash\\": \\"1234567812345678123456781234567812345678\\", '
          '\\"repository\\": \\"src\\"}], '
          '\\"patch\\": {\\"change\\": 456789, \\"revision\\": 12, '
          '\\"server\\": \\"https://chromium-review.googlesource.com\\"}}", '
          '"isolate_map": "{\\"base_unittests\\": '
          '\\"[dummy hash for base_unittests/dummy size]\\"}", '
          '"isolate_server": '
          '"projects/example-cas-server/instances/default_instance"}',
      ]),
      api.post_process(post_process.DropExpectation),
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
      api.properties(swarming_gtest=True),
      # The important bit here is the lack of a second "git_hash" entry and the
      # use of the v8 repo.
      api.
      post_process(post_process.StepCommandContains, 'pinpoint isolate upload', [
          '{"builder_name": "linux-builder-perf", '
          '"change": "{\\"commits\\": ['
          '{\\"git_hash\\": \\"1234abcd1234abcd1234abcd1234abcd1234abcd\\", '
          '\\"repository\\": \\"v8\\"}], '
          '\\"patch\\": {\\"change\\": 456789, \\"revision\\": 12, '
          '\\"server\\": \\"https://chromium-review.googlesource.com\\"}}", '
          '"isolate_map": "{\\"base_unittests\\": '
          '\\"[dummy hash for base_unittests/dummy size]\\"}", '
          '"isolate_server": '
          '"projects/example-cas-server/instances/default_instance"}',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'perf_isolate_lookup_no_commit',
      # pinpoint/builder.py does its own mapping of try builder to CI builder
      # because it wants a simple mapping that pulls in all triggered try
      # builders, which doesn't match the semantics of trybot/TrySpec
      api.chromium.try_build(
          builder='linux-builder-perf',
          builder_group='chromium.perf',
      ),
      api.bot_update.revisions(
          {'src': 'f27fede2220bcd326aee3e86ddfd4ebd0fe58cb9'}),
      api.properties(swarming_gtest=True),
      # the important bit here is the lack of a second "git_hash" entry.
      api.
      post_process(post_process.StepCommandContains, 'pinpoint isolate upload', [
          '{"builder_name": "linux-builder-perf", '
          '"change": "{\\"commits\\": ['
          '{\\"git_hash\\": \\"f27fede2220bcd326aee3e86ddfd4ebd0fe58cb9\\", '
          '\\"repository\\": \\"chromium\\"}], '
          '\\"patch\\": {\\"change\\": 456789, \\"revision\\": 12, '
          '\\"server\\": \\"https://chromium-review.googlesource.com\\"}}", '
          '"isolate_map": "{\\"base_unittests\\": '
          '\\"[dummy hash for base_unittests/dummy size]\\"}", '
          '"isolate_server": '
          '"projects/example-cas-server/instances/default_instance"}',
      ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'android',
      api.platform.arch('arm'),
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake.group', builder='Cronet', builder_db=BUILDERS),
      api.post_process(post_process.StepCommandContains, 'lookup GN args', [
          '-m',
          'fake.group',
          '-b',
          'Cronet',
      ]),
      api.post_process(post_process.MustRun, 'tree truth steps'),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'android_test_trigger_cas',
      api.platform('linux', 64),
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake.group',
          builder='Android Tester CAS',
          builder_db=BUILDERS),
      api.properties(
          test_trigger_deps_digest='fake-digest/123',
          parent_got_revision='fake-parent-revision',
      ),
      api.post_process(post_process.DoesNotRun, 'tree truth steps'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'android_version',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake.group',
          builder='Test Version',
          builder_db=BUILDERS),
      api.chromium.override_version(major=123, minor=1, build=9876, patch=2),
      api.post_process(post_process.StepCommandContains, 'lookup GN args', [
          '--android-version-code=987600200',
          '--android-version-name=123.1.9876.2',
      ]),
      api.post_process(post_process.MustRun, 'compile'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'run_mb_and_compile_cleandead',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          experiments=['chromium.enable_cleandead'],
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.post_check(post_process.MustRun, 'cleandead'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failing_cleandead',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-try-builder',
          experiments=['chromium.enable_cleandead'],
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.step_data('cleandead', retcode=1),
      api.post_check(post_process.MustRun,
                     'generate_build_files (with patch) (2)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_experimentally_enabled_build_full_run',
      api.properties(
          **{
              "$recipe_engine/cv": {
                  "active": True,
                  "dryRun": True,
                  "runMode": "FULL_RUN",
                  "topLevel": True
              }
          }),
      api.chromium_tests_builder_config.try_build(
          builder_group='tryserver.chromium.test',
          builder='rts-rel',
          builder_db=BUILDERS,
          try_db=_TEST_TRYBOTS,
          experiments=['chromium_rts.filter_file_analysis'],
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarming_gtest=True),
      api.post_process(post_process.MustRun,
                       'generate chromium-rts filter files'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_experimentally_enabled_build_dry_run',
      api.properties(
          **{
              "$recipe_engine/cv": {
                  "active": True,
                  "dryRun": True,
                  "runMode": "DRY_RUN",
                  "topLevel": True
              }
          }),
      api.chromium_tests_builder_config.try_build(
          builder_group='tryserver.chromium.test',
          builder='rts-rel',
          builder_db=BUILDERS,
          try_db=_TEST_TRYBOTS,
          experiments=['chromium_rts.filter_file_analysis'],
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarming_gtest=True),
      api.post_process(post_process.MustRun,
                       'generate chromium-rts filter files'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_skip_tests_enabled',
      api.properties(
          **{
              "$recipe_engine/cv": {
                  "active": True,
                  "dryRun": True,
                  "runMode": "DRY_RUN",
                  "topLevel": True
              }
          }),
      api.chromium_tests_builder_config.try_build(
          builder_group='tryserver.chromium.test',
          builder='rts-rel',
          builder_db=BUILDERS,
          try_db=_TEST_TRYBOTS,
          experiments=['chromium_rts.filter_file_analysis'],
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(swarming_gtest=True),
      api.path.exists(
          api.path.cache_dir / 'builder' / 'src' / 'out' / 'd04d-rts-rel' /
          'gen' / 'rts' / 'base_unittests.filter',
          api.path.cache_dir / 'builder' / 'src' / 'out' / 'd04d-rts-rel' /
          'base_unittests.isolate',
      ),
      api.override_step_data(
          'add RTS filter files to isolates.Read [CACHE]/builder/src/out/d04d-rts-rel/base_unittests.isolate',
          api.file.read_json({'variables': {
              'files': []
          }})),
      api.post_process(post_process.MustRun,
                       'generate chromium-rts filter files'),
      api.post_process(
          post_process.MustRun,
          'add RTS filter files to isolates.Read [CACHE]/builder/src/out/d04d-rts-rel/base_unittests.isolate'
      ),
      api.post_process(
          post_process.MustRun,
          'add RTS filter files to isolates.Write [CACHE]/builder/src/out/d04d-rts-rel/base_unittests.isolate'
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_skip_tests_banned',
      api.properties(
          **{
              "$recipe_engine/cv": {
                  "active": True,
                  "dryRun": True,
                  "runMode": "DRY_RUN",
                  "topLevel": True
              }
          }),
      api.chromium_tests_builder_config.try_build(
          builder_group='tryserver.chromium.test',
          builder='rts-rel',
          builder_db=BUILDERS,
          try_db=_TEST_TRYBOTS,
          experiments=['chromium_rts.filter_file_analysis'],
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
              builder_group='fake-group',
              builder='fake-builder',
          ).assemble()),
      api.properties(
          swarming_gtest=True,
          test_target_name='blink_python_tests',
      ),
      api.path.exists(
          api.path.cache_dir / 'builder' / 'src' / 'out' / 'd04d-rts-rel' /
          'gen' / 'rts' / 'blink_python_tests.filter',
          api.path.cache_dir / 'builder' / 'src' / 'out' / 'd04d-rts-rel' /
          'blink_python_tests.isolate',
      ),
      api.post_process(post_process.MustRun,
                       'generate chromium-rts filter files'),
      api.post_process(post_process.MustRun,
                       'add RTS filter files to isolates'),
      api.post_process(
          post_process.DoesNotRun,
          'add RTS filter files to isolates.Read [CACHE]/builder/src/out/d04d-rts-rel/blink_python_tests.isolate'
      ),
      api.post_process(post_process.DropExpectation),
  )
