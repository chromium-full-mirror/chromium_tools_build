# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from PB.recipe_modules.build.chromium_orchestrator.properties import (
    InputProperties)
from PB.infra.chromium import chromium_bootstrap
from PB.go.chromium.org.luci.swarming.proto.api import swarming as swarming_pb
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

DEPS = [
    'chromium',
    'chromium_bootstrap',
    'chromium_orchestrator',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'depot_tools/tryserver',
    'recipe_engine/file',
    'recipe_engine/led',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'test_utils',
]


def RunSteps(api):
  assert api.tryserver.is_tryserver
  api.chromium_swarming.set_default_dimension('os', 'Linux')
  return api.chromium_orchestrator.trybot_steps()


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  def ctbc_properties():
    return ctbc_api.properties(
        ctbc_api.properties_assembler_for_try_builder().with_mirrored_builder(
            builder_group='fake-group',
            builder='fake-builder',
        ).with_mirrored_tester(
            builder_group='fake-group',
            builder='fake-tester',
        ).assemble())

  cas_digest_hash = (
      "20688f6c4da520c005b5c9faa1e9f2bc8cf21fef55b7e17e31cc5d4c346e7974")
  cas_digest_size = 71
  expected_cas_ref = '{}/{}'.format(
      cas_digest_hash,
      str(cas_digest_size),
  )

  def setup(use_cipd_exe=False, real_build=True):
    if use_cipd_exe:
      exe = chromium_bootstrap.BootstrappedExe(
          cipd=chromium_bootstrap.Cipd(
              server='https://chrome-infra-packages.appspot.com',
              package=(
                  'infra/recipe_bundles/chromium.googlesource.com/chromium/'
                  'tools/build'),
              requested_version='refs/heads/main',
              actual_version='HjSXE0bSX9-x_3wX0A2Y1yyiwJiWHuJ-cSgPcnRghcQC',
          ),
          cmd='luciexe',
      )
    else:
      exe = chromium_bootstrap.BootstrappedExe(
          cas=swarming_pb.CASReference(
              cas_instance=(
                  'projects/chromium-swarm/instances/default_instance'),
              digest=swarming_pb.Digest(
                  hash=cas_digest_hash,
                  size_bytes=cas_digest_size,
              ),
          ),
          cmd='luciexe',
      )

    led_property = {
        'led_run_id': 'chromium/led/kimstephanie_google.com/88a27cab21a8ad2',
    }
    if real_build:
      led_property = {
          'shadowed_bucket': 'try',
      }

    return sum([
        api.chromium.try_build(
            builder_group='fake-try-group',
            builder='fake-orchestrator',
            experiments=['other_experiment_name'],
        ),
        ctbc_properties(),
        api.chromium_bootstrap.properties(exe=exe),
        api.code_coverage(use_clang_coverage=True),
        api.properties(
            **{
                '$build/chromium_orchestrator':
                    InputProperties(
                        compilator='fake-compilator',
                        compilator_watcher_git_revision='e841fc',
                    ),
                '$recipe_engine/led':
                    led_property,
            }),
    ], api.empty_test_data())

  yield api.test(
      'raw-led',
      setup(real_build=False),
      api.expect_status('INFRA_FAILURE'),
      api.post_check(
          post_process.SummaryMarkdownRE,
          'using led swarming tasks is not supported for this recipe'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'trigger_led_compilator',
      api.chromium.try_build(
          bucket="try.shadow",
          builder_group='fake-try-group',
          builder='fake-orchestrator',
      ),
      setup(),
      api.chromium_orchestrator.override_led_get_builder(),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_swarming_phase=False),
      api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
      api.post_process(
          post_process.StepCommandContains,
          'trigger compilator (with patch).led get-builder',
          [
              'led', 'get-builder', '-adjust-priority', '0', '-real-build',
              'luci.chromium.try:fake-compilator'
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          'trigger compilator (with patch).led edit-payload',
          ['-cas-ref', expected_cas_ref],
      ),
      api.post_process(
          post_process.StepCommandContains,
          'trigger compilator (with patch).led launch',
          [
              'led', 'launch', '-resultdb', 'on', '-bound-to-parent',
              '-real-build'
          ],
      ),
      api.post_process(post_process.MustRun, 'compilator steps (with patch)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'trigger_led_compilator_with_no_recipe_changes',
      api.chromium.try_build(
          bucket="try.shadow",
          builder_group='fake-try-group',
          builder='fake-orchestrator',
      ),
      setup(use_cipd_exe=True),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_led_get_builder(),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_swarming_phase=False),
      api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
      api.post_process(
          post_process.DoesNotRun,
          'trigger compilator (with patch).led edit-payload',
      ),
      api.post_process(post_process.MustRun,
                       'downloading cas digest all_test_binaries'),
      api.post_process(post_process.MustRun, 'download src-side deps'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'trigger_without_patch_led_compilator',
      api.chromium.try_build(
          bucket="try.shadow",
          builder_group='fake-try-group',
          builder='fake-orchestrator',
      ),
      setup(),
      api.chromium_orchestrator.override_led_get_builder(),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_swarming_phase=False),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'without patch', failures=['Test.One']),
      api.post_process(post_process.MustRun,
                       'trigger compilator (without patch)'),
      api.post_process(
          post_process.StepCommandContains,
          'trigger compilator (without patch).led get-builder',
          [
              'led', 'get-builder', '-adjust-priority', '0', '-real-build',
              'luci.chromium.try:fake-compilator'
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          'trigger compilator (without patch).led launch',
          [
              'led', 'launch', '-resultdb', 'on', '-bound-to-parent',
              '-real-build'
          ],
      ),
      api.post_process(post_process.MustRun,
                       'compilator steps (without patch)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip_without_patch_local_tests_failed',
      api.chromium.try_build(
          bucket="try.shadow",
          builder_group='fake-try-group',
          builder='fake-orchestrator',
      ),
      setup(),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_led_get_builder(),
      api.chromium_orchestrator.override_compilator_steps(
          affected_files=['src/testing/buildbot/fake-group.json']),
      api.chromium_orchestrator.override_compilator_steps(
          is_swarming_phase=False,
          sub_build_summary=("1 Test Suite(s) failed.\n\n"
                             "**headless_python_unittests** failed."),
          sub_build_status=common_pb.FAILURE,
      ),
      api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
      api.post_process(
          post_process.StepCommandContains,
          'trigger compilator (with patch).led edit-payload',
          ['-cas-ref', expected_cas_ref],
      ),
      api.post_process(post_process.MustRun,
                       'downloading cas digest all_test_binaries'),
      api.post_process(post_process.MustRun, 'download src-side deps'),
      api.post_process(post_process.DoesNotRun,
                       'trigger compilator (without patch)'),
      api.post_process(post_process.DoesNotRun,
                       'collect compilator (without patch)'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )
