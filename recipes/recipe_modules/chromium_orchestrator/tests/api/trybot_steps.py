# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine import post_process
from PB.recipe_modules.build.chromium_orchestrator.properties import (
    InputProperties)

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.depot_tools.tryserver import api as tryserver
from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec
from RECIPE_MODULES.build.chromium_orchestrator.api import (
    COMPILATOR_SWARMING_TASK_COLLECT_STEP)
from RECIPE_MODULES.build.chromium_orchestrator.api import (
    BUILD_CANCELED_SUMMARY)
from RECIPE_MODULES.build.chromium_orchestrator.api import (RTS_SUMMARY)

from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import common as resultdb_common
from PB.go.chromium.org.luci.resultdb.proto.v1 \
    import test_result as test_result_pb2
from PB.go.chromium.org.luci.analysis.proto.v1 import test_history

DEPS = [
    'chromium',
    'chromium_orchestrator',
    'chromium_swarming',
    'chromium_tests',
    'chromium_tests_builder_config',
    'code_coverage',
    'depot_tools/gclient',
    'depot_tools/gitiles',
    'depot_tools/tryserver',
    'filter',
    'flakiness',
    'profiles',
    'recipe_engine/buildbucket',
    'recipe_engine/cv',
    'recipe_engine/cq',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/luci_analysis',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'skylab',
    'test_utils',
]


def RunSteps(api):
  assert api.tryserver.is_tryserver
  api.path.mock_add_paths(
      api.profiles.profile_dir().joinpath('overall-merged.profdata'))
  api.chromium_swarming.set_default_dimension('os', 'Linux')
  return api.chromium_orchestrator.trybot_steps()


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  def ctbc_properties(**kwargs):
    return ctbc_api.properties(
        ctbc_api.properties_assembler_for_try_builder(
            **kwargs).with_mirrored_builder(
                builder_group='fake-group',
                builder='fake-builder',
            ).with_mirrored_tester(
                builder_group='fake-group',
                builder='fake-tester',
            ).assemble())

  def get_try_build(builder='fake-orchestrator'):
    return api.chromium.try_build(
        builder_group='fake-try-group',
        builder=builder,
        revision='d3advegg13',
        tags=api.buildbucket.tags(
            cq_equivalent_cl_group_key='12345', cq_attempt_key='67890'),
    )

  yield api.test(
      'basic',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.code_coverage(use_clang_coverage=True),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.post_process(
          post_process.StepCommandContains,
          'install infra/chromium/compilator_watcher.ensure_installed', [
              '-ensure-file', 'infra/chromium/compilator_watcher/${platform} '
              'git_revision:e841fc'
          ]),
      api.post_process(post_process.MustRun, 'set_output_gitiles_commit'),
      api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
      api.post_process(post_process.MustRun, 'browser_tests (with patch)'),
      api.post_process(post_process.MustRun,
                       'downloading cas digest all_test_binaries'),
      api.post_process(post_process.MustRun,
                       COMPILATOR_SWARMING_TASK_COLLECT_STEP),
      api.post_process(post_process.MustRun, 'ensure clang coverage tools'),
      api.post_process(post_process.MustRun, 'download src-side deps'),
      api.post_process(
          post_process.StepTextContains,
          'read test spec (fake-group.json)',
          ['[CLEANUP]/src/testing/buildbot/fake-group.json'],
      ),
      api.post_process(post_process.LogDoesNotContain,
                       'trigger compilator (with patch)', 'request',
                       ['rts_model']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_branch',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.tryserver.gerrit_change_target_ref('refs/branch-heads/4472'),
      api.code_coverage(use_clang_coverage=True),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
      api.post_process(post_process.MustRun, 'browser_tests (with patch)'),
      api.post_process(post_process.MustRun,
                       'downloading cas digest all_test_binaries'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'non_src_CL',
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium.try_build(
          builder='fake-orchestrator',
          git_repo='https://chromium.googlesource.com/v8/v8',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      api.code_coverage(use_clang_coverage=True),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
      api.post_process(post_process.MustRun, 'browser_tests (with patch)'),
      api.post_process(post_process.MustRun,
                       'downloading cas digest all_test_binaries'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_with_skylab',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(
          include_swarming_props=False,
          include_skylab_props=True,
      ),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          skylab_tests=['lacros_all_tast_tests'],
      ),
      api.skylab.mock_wait_on_suites('lacros_all_tast_tests (with patch)', 1),
      api.override_step_data(
          'lacros_all_tast_tests results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'lacros_all_tast_tests', passing_tests=['Test.Two']))),
      api.post_process(post_process.MustRun, 'set_output_gitiles_commit'),
      api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
      api.post_process(post_process.MustRun, (
          'test_pre_run (with patch).lacros_all_tast_tests (with patch).schedule'
      )),
      api.post_process(post_process.MustRun,
                       COMPILATOR_SWARMING_TASK_COLLECT_STEP),
      api.post_process(post_process.MustRun, 'download src-side deps'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'without_patch_compilator',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'without patch', failures=['Test.One']),
      api.post_process(post_process.MustRun,
                       'trigger compilator (without patch)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'without_patch_compilator_skylab',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(
          include_swarming_props=False,
          include_skylab_props=True,
      ),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(
          include_swarming_props=False,
          include_skylab_props=True,
          with_patch=False,
      ),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          skylab_tests=['lacros_all_tast_tests'],
      ),
      api.skylab.mock_wait_on_suites('lacros_all_tast_tests (with patch)', 1),
      api.skylab.mock_wait_on_suites('lacros_all_tast_tests (without patch)',
                                     1),
      # with patch
      api.override_step_data(
          'lacros_all_tast_tests results',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'lacros_all_tast_tests', failing_tests=['Test.Two']))),
      # retry with patch
      api.override_step_data(
          'lacros_all_tast_tests results (2)',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'lacros_all_tast_tests', failing_tests=['Test.Two']))),
      # without patch
      api.override_step_data(
          'lacros_all_tast_tests results (3)',
          stdout=api.raw_io.output_text(
              api.test_utils.rdb_results(
                  'lacros_all_tast_tests', failing_tests=['Test.Two']))),
      api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
      api.post_process(post_process.MustRun,
                       'trigger compilator (without patch)'),
      api.post_process(post_process.MustRun, (
          'test_pre_run (with patch).lacros_all_tast_tests (with patch).schedule'
      )),
      api.post_process(post_process.MustRun, (
          'test_pre_run (without patch).lacros_all_tast_tests (without patch).schedule'
      )),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'without_patch_compilator_non_src_CL',
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium.try_build(
          builder='fake-orchestrator',
          git_repo='https://chromium.googlesource.com/v8/v8',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      api.chromium_orchestrator.override_compilator_steps(
          include_override_deps=True),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False, include_override_deps=True),
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
          post_process.LogContains,
          'trigger compilator (without patch)',
          'request',
          ['deps_revision_overrides', 'src/v8'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'coverage_not_enabled',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.post_process(post_process.DoesNotRun,
                       'run tools/clang/scripts/update.py'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'clang_coverage_not_enabled',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.code_coverage(use_java_coverage=True),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.post_process(post_process.MustRun,
                       'downloading cas digest all_test_binaries'),
      api.post_process(post_process.DoesNotRun,
                       'run tools/clang/scripts/update.py'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_builder_to_trigger_passed_in',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      api.post_process(post_process.DoesNotRun,
                       'trigger compilator (with patch)'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'depend_on_footer_failure',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.step_data(
          'parse description',
          api.json.output(
              {tryserver.constants.CQ_DEPEND_FOOTER: 'chromium:123456'})),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdownRE,
                       r'Commit message footer Cq-Depend is not supported.*'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'missing_compilator_watcher_git_revision',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(compilator='fake-compilator',),
          }),
      api.code_coverage(use_clang_coverage=True),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.post_process(
          post_process.StepCommandContains,
          'install infra/chromium/compilator_watcher.ensure_installed', [
              '-ensure-file',
              'infra/chromium/compilator_watcher/${platform} latest'
          ]),
      api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
      api.post_process(post_process.MustRun, 'browser_tests (with patch)'),
      api.post_process(post_process.MustRun,
                       'downloading cas digest all_test_binaries'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'missing_compilator',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(compilator_watcher_git_revision='e841fc'),
          }),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'sub_build_canceled_status_and_in_global_shutdown',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.runtime.global_shutdown_on_step('get_invocation_instructions'),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(
          sub_build_status=common_pb.CANCELED, empty_props=True),
      api.post_process(post_process.SummaryMarkdownRE, BUILD_CANCELED_SUMMARY),
      api.expect_status('CANCELED'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'dont_fetch_comp_build_if_canceled',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.runtime.global_shutdown_on_step('compilator gitiles_commit missing'),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(
          sub_build_status=common_pb.INFRA_FAILURE,
          empty_props=True,
          empty_gitiles_commit=True),
      api.post_process(post_process.DoesNotRun, 'fetch compilator build proto'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'dont_collect_comp_task_if_not_ended',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.runtime.global_shutdown_on_step('download command lines'),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.post_process(post_process.DoesNotRun,
                       COMPILATOR_SWARMING_TASK_COLLECT_STEP),
      api.expect_status('CANCELED'),
      api.post_process(post_process.DropExpectation),
  )

  fake_rts_command_lines = {
      'browser_tests': [
          './%s' % 'browser_tests', '--fake-without-patch-flag',
          '--fake-log-file', '$ISOLATED_OUTDIR/fake.log',
          '-filter=browser_tests.filter'
      ]
  }
  yield api.test(
      'rts_full_run',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-group',
          builder='fake-tester',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-tester':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              }
          }),
          try_db=ctbc.TryDatabase.create({
              'fake-group': {
                  'fake-tester':
                      ctbc.TrySpec.create(
                          mirrors=[
                              ctbc.TryMirror.create(
                                  builder_group='fake-group',
                                  buildername='fake-tester',
                                  tester='fake-tester',
                              ),
                          ],
                          regression_test_selection=try_spec.ALWAYS,
                      ),
              }
          }),
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      api.cv(run_mode='FULL_RUN', top_level=True),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['browser_tests', 'content_unittests']),
      api.step_data('read command lines (2)',
                    api.file.read_json(fake_rts_command_lines)),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.post_process(post_process.MustRun, 'RTS was used'),
      api.post_process(post_process.MustRun, 'rts options'),
      api.post_process(
          post_process.LogContains,
          'trigger compilator (with patch)',
          'request',
          ['$recipe_engine/cq', 'FULL_RUN'],
      ),
      api.post_process(post_process.PropertyEquals, 'rts_was_used', True),
      api.post_process(post_process.LogContains,
                       'trigger compilator (with patch)', 'request',
                       ['"rts_model": "chromium-rts"']),
      api.post_process(post_process.DropExpectation),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'rts_full_run_not_used',
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-group',
          builder='fake-tester',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-tester':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              }
          }),
          try_db=ctbc.TryDatabase.create({
              'fake-group': {
                  'fake-tester':
                      ctbc.TrySpec.create(
                          mirrors=[
                              ctbc.TryMirror.create(
                                  builder_group='fake-group',
                                  buildername='fake-tester',
                                  tester='fake-tester',
                              ),
                          ],
                          regression_test_selection=try_spec.ALWAYS,
                      ),
              }
          }),
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.cq(run_mode='FULL_RUN', top_level=True),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['browser_tests', 'content_unittests']),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.post_process(post_process.DoesNotRun, 'RTS was used'),
      api.post_process(post_process.MustRun, 'rts options'),
      api.post_process(
          post_process.LogContains,
          'trigger compilator (with patch)',
          'request',
          ['$recipe_engine/cq', 'FULL_RUN'],
      ),
      api.post_process(post_process.PropertiesDoNotContain, 'rts_was_used'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'rts_with_unrecoverable_tests',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-group',
          builder='fake-tester',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-tester':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              }
          }),
          try_db=ctbc.TryDatabase.create({
              'fake-group': {
                  'fake-tester':
                      ctbc.TrySpec.create(
                          mirrors=[
                              ctbc.TryMirror.create(
                                  builder_group='fake-group',
                                  buildername='fake-tester',
                                  tester='fake-tester',
                              ),
                          ],
                          regression_test_selection=try_spec.ALWAYS,
                      ),
              }
          }),
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      api.cv(run_mode='FULL_RUN', top_level=True),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False,
          sub_build_summary=("1 Test Suite(s) failed.\n\n"
                             "**headless_python_unittests** failed."),
          sub_build_status=common_pb.FAILURE,
      ),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'content_unittests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.post_process(post_process.MustRun, 'browser_tests (without patch)'),
      api.post_process(post_process.DoesNotRun,
                       'content_unittests (without patch)'),
      api.post_process(post_process.SummaryMarkdownRE, RTS_SUMMARY),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'rts_with_invalid_tests',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      api.chromium_tests_builder_config.try_build(
          builder_group='fake-group',
          builder='fake-tester',
          builder_db=ctbc.BuilderDatabase.create({
              'fake-group': {
                  'fake-tester':
                      ctbc.BuilderSpec.create(
                          chromium_config='chromium',
                          gclient_config='chromium',
                      ),
              }
          }),
          try_db=ctbc.TryDatabase.create({
              'fake-group': {
                  'fake-tester':
                      ctbc.TrySpec.create(
                          mirrors=[
                              ctbc.TryMirror.create(
                                  builder_group='fake-group',
                                  buildername='fake-tester',
                                  tester='fake-tester',
                              ),
                          ],
                          regression_test_selection=try_spec.ALWAYS,
                      ),
              }
          }),
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      api.cv(run_mode='FULL_RUN', top_level=True),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.override_step_data(
          'browser_tests (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results('invalid', retcode=1),
              failure=True)),
      api.override_step_data(
          'browser_tests (retry shards with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results('invalid', retcode=1),
              failure=True)),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.post_process(post_process.SummaryMarkdownRE, RTS_SUMMARY),
      api.post_process(post_process.MustRun,
                       'browser_tests (retry shards with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'browser_tests (without patch)'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_tests_to_trigger',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_compilator_steps(empty_props=True),
      api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
      api.post_process(post_process.DoesNotRun, 'browser_tests (with patch)'),
      api.post_process(post_process.LogContains,
                       'trigger compilator (with patch)', 'request',
                       ['fake-try-group', 'fake-compilator']),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retry_shards',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.post_process(post_process.DoesNotRun,
                       'trigger compilator (without patch)'),
      api.post_process(post_process.MustRun,
                       'browser_tests (retry shards with patch)'),
      api.post_process(post_process.MustRun, 'Test statistics'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retry_shards_skip_without_patch',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
          experiments=['chromium.pre_retry_shards_without_patch_compile'],
      ),
      ctbc_properties(retry_without_patch=False),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.post_process(post_process.DoesNotRun,
                       'trigger compilator (without patch)'),
      api.post_process(post_process.MustRun,
                       'browser_tests (retry shards with patch)'),
      api.post_process(post_process.DropExpectation),
  )

  # without patch fails so failure is not due to CL
  yield api.test(
      'retry_shards_without_patch_fails_tryjob_succeeds',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'content_unittests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'without patch', failures=['Test.One']),
      api.post_process(post_process.MustRun,
                       'trigger compilator (without patch)'),
      api.post_process(post_process.MustRun, 'browser_tests (without patch)'),
      api.post_process(post_process.DoesNotRun,
                       'content_unittests (without patch)'),
      api.post_process(post_process.DropExpectation),
  )

  def CheckTestStatus(check, step_odict, test_target, expected):
    target_status = post_process.GetBuildProperties(step_odict).get(
        'test_status', '').get(test_target, '')
    check(target_status == expected)
  # without patch passes so failure is due to CL
  yield api.test(
      'retry_without_patch_passes_tryjob_fails',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'content_unittests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.post_process(post_process.MustRun, 'browser_tests (without patch)'),
      api.post_process(post_process.MustRun, 'record test suite statuses'),
      api.post_process(CheckTestStatus, 'browser_tests', 'Failure'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retry_shards_invalid',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.override_step_data(
          'browser_tests (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results(test_results_json='', retcode=1),
              failure=True)),
      api.override_step_data(
          'browser_tests (retry shards with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_gtest_output(passing=True), failure=False)),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.post_process(post_process.MustRun,
                       'browser_tests (retry shards with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'browser_tests (without patch)'),
      api.post_process(post_process.MustRun, 'record test suite statuses'),
      api.post_process(CheckTestStatus, 'browser_tests', 'Success'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skips_successful_tests',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          revision='d3advegg13',
          tags=api.buildbucket.tags(
              cq_equivalent_cl_group_key='12345', cq_attempt_key='67890')),
      api.cq(run_mode='FULL_RUN'),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.code_coverage(use_clang_coverage=True),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['browser_tests', 'unit_tests']),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_orchestrator.override_test_spec(
          tests=['browser_tests', 'unit_tests'],
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_tests.simulate_previous_build(
          test_statuses={'unit_tests': 'Success'}),
      api.post_process(post_process.MustRun, 'unit_tests (with patch)'),
      api.post_process(
          post_process.StepTextEquals, 'unit_tests (with patch)',
          ('This test is not being run because it has passed in the last 24 '
           'hours with the equivalent patchset')),
      api.post_process(post_process.MustRun, 'browser_tests (with patch)'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skips_all_tests_from_reuse',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          revision='d3advegg13',
          tags=api.buildbucket.tags(
              cq_equivalent_cl_group_key='12345', cq_attempt_key='67890')),
      api.cq(run_mode='FULL_RUN'),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.code_coverage(use_clang_coverage=True),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_orchestrator.override_test_spec(
          tests=['unit_tests'],
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_tests.simulate_previous_build(
          test_statuses={'unit_tests': 'Success'}),
      api.post_process(post_process.MustRun, 'unit_tests (with patch)'),
      api.post_process(
          post_process.StepTextEquals, 'unit_tests (with patch)',
          ('This test is not being run because it has passed in the last 24 '
           'hours with the equivalent patchset')),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retry_shards_invalid_retry',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.post_process(post_process.MustRun,
                       'browser_tests (retry shards with patch)'),
      api.post_process(post_process.MustRun, 'browser_tests (without patch)'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retry_shards_all_invalid_results',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.override_step_data(
          'browser_tests (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results('invalid', retcode=1),
              failure=True)),
      api.override_step_data(
          'browser_tests (retry shards with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.gtest_results('invalid', retcode=1),
              failure=True)),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.post_process(post_process.MustRun,
                       'browser_tests (retry shards with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'browser_tests (without patch)'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'skip_without_patch_local_tests_failed',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(
          affected_files=['src/testing/buildbot/fake-group.json']),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False,
          sub_build_summary=("1 Test Suite(s) failed.\n\n"
                             "**headless_python_unittests** failed."),
          sub_build_status=common_pb.FAILURE,
      ),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.post_process(post_process.DoesNotRun,
                       'trigger compilator (without patch)'),
      api.post_process(post_process.MustRun,
                       'browser_tests (retry shards with patch)'),
      api.post_process(post_process.SummaryMarkdownRE,
                       '.*headless_python_unittests.*'),
      api.post_process(post_process.SummaryMarkdownRE, '.*browser_tests.*'),
      api.post_process(post_process.DoesNotRun,
                       'browser_tests (without patch)'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retry_without_patch_passes_local_tests_failed',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False,
          sub_build_summary=("1 Test Suite(s) failed.\n\n"
                             "**headless_python_unittests** failed."),
          sub_build_status=common_pb.FAILURE,
      ),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'content_unittests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.post_process(post_process.MustRun, 'browser_tests (without patch)'),
      api.post_process(post_process.DoesNotRun,
                       'content_unittests (without patch)'),
      api.post_process(post_process.SummaryMarkdownRE,
                       '.*headless_python_unittests.*'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'compilator_skipping_coverage',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.code_coverage(use_clang_coverage=True),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(
          skipping_coverage=True),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.post_process(post_process.MustRun, 'browser_tests (with patch)'),
      api.post_process(post_process.DoesNotRun,
                       'downloading cas digest all_test_binaries'),
      api.post_process(post_process.MustRun, 'download src-side deps'),
      api.post_process(
          # Only generates coverage data for the with patch step.
          post_process.DoesNotRun,
          'process clang code coverage data for overall test '
          'coverage.generate metadata for overall test coverage in 1 '
          'tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'code_coverage_trybot_with_patch',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.code_coverage(use_clang_coverage=True),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.post_process(post_process.MustRun, 'browser_tests (with patch)'),
      api.post_process(post_process.MustRun,
                       'downloading cas digest all_test_binaries'),
      api.post_process(
          # Only generates coverage data for the with patch step.
          post_process.MustRun,
          'process clang code coverage data for overall test '
          'coverage.generate metadata for overall test coverage in 1 '
          'tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'code_coverage_trybot_retry_shards_with_patch',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.code_coverage(use_clang_coverage=True),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.override_step_data(
          'browser_tests (with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_gtest_output(False), failure=True)),
      api.override_step_data(
          'browser_tests (retry shards with patch)',
          api.chromium_swarming.canned_summary_output(
              api.test_utils.canned_gtest_output(True), failure=False)),
      api.post_process(post_process.MustRun, 'browser_tests (with patch)'),
      api.post_process(post_process.MustRun,
                       'downloading cas digest all_test_binaries'),
      api.post_process(
          # Only generates coverage data for the with patch step.
          post_process.MustRun,
          'process clang code coverage data for overall test '
          'coverage.generate metadata for overall test coverage in 2 '
          'tests'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'code_coverage_trybot_without_patch',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.code_coverage(use_clang_coverage=True),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.post_process(post_process.MustRun,
                       'downloading cas digest all_test_binaries'),
      api.post_process(post_process.MustRun, 'browser_tests (without patch)'),
      api.post_process(
          # Generates coverage data for the with patch and retry shards with
          # patch steps. Without patch steps are always ignored.
          post_process.MustRun,
          'process clang code coverage data for overall test coverage.generate '
          'metadata for overall test coverage in 2 tests'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  def StepTextDoesNotContain(check, step_odict, step, unexpected_substrs):
    for unexpected in unexpected_substrs:
      check(unexpected not in step_odict[step].step_text)

  yield api.test(
      'retry_without_patch_fails_tests_and_local_tests',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False,
          sub_build_summary=("1 Test Suite(s) failed.\n\n"
                             "**headless_python_unittests** failed."),
          sub_build_status=common_pb.FAILURE,
      ),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests',
          'without patch',
          failures=['Test.One'],
          expected_skips=['Test.Two']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'content_unittests', 'with patch', failures=['Test.One']),
      api.post_process(post_process.SummaryMarkdownRE,
                       '.*headless_python_unittests.*'),
      api.post_process(post_process.MustRun,
                       'trigger compilator (without patch)'),
      api.post_process(StepTextDoesNotContain, 'browser_tests (without patch)',
                       ['Test.Two']),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'without_patch_compilator_missing_swarming_props',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=False, empty_props=True),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.post_process(post_process.MustRun,
                       'trigger compilator (without patch)'),
      api.post_process(post_process.DoesNotRun,
                       'browser_tests (without patch)'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failed_compilator_while_waiting_for_swarming_props',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(
          status=common_pb.FAILURE),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(
          sub_build_status=common_pb.FAILURE,
          empty_props=True,
          sub_build_summary='Step compile (with patch) failed.'),
      api.post_process(post_process.SummaryMarkdown,
                       'Step compile (with patch) failed.'),
      api.post_process(post_process.MustRun,
                       COMPILATOR_SWARMING_TASK_COLLECT_STEP),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failed_wo_patch_compilator',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_schedule_compilator_build(
          step_name='trigger compilator (with patch)',
          build_id=12345,
      ),
      api.chromium_orchestrator.override_schedule_compilator_build(
          step_name='trigger compilator (without patch)',
          build_id=54321,
      ),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(
          build_id=54321, status=common_pb.FAILURE),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=False,
          empty_props=True,
          sub_build_status=common_pb.FAILURE),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.post_process(post_process.MustRun,
                       'trigger compilator (without patch)'),
      api.post_process(post_process.MustRun,
                       COMPILATOR_SWARMING_TASK_COLLECT_STEP),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'infra_failed_compilator_while_waiting_for_swarming_props',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(
          status=common_pb.INFRA_FAILURE),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(
          sub_build_status=common_pb.INFRA_FAILURE,
          empty_props=True,
          sub_build_summary='Timeout waiting for compilator build'),
      api.post_process(post_process.SummaryMarkdown,
                       'Timeout waiting for compilator build'),
      api.post_process(post_process.MustRun,
                       COMPILATOR_SWARMING_TASK_COLLECT_STEP),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'canceled_compilator_while_waiting_for_swarming_props',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_compilator_steps(
          sub_build_status=common_pb.CANCELED,
          empty_props=True,
          sub_build_summary='Canceled'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'failed_compilator_local_test',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(
          status=common_pb.FAILURE),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False,
          sub_build_status=common_pb.FAILURE,
          sub_build_summary=("1 Test Suite(s) failed.\n\n"
                             "**headless_python_unittests** failed.")),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.post_process(post_process.MustRun,
                       COMPILATOR_SWARMING_TASK_COLLECT_STEP),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'compilator_canceled_at_local_test_phase',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          sub_build_status=common_pb.CANCELED,
          is_compile_phase=False,
          sub_build_summary='Canceled'),
      api.chromium_orchestrator.override_compilator_steps(),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'sub_build_infra_failed',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.chromium_orchestrator.override_compilator_steps(
          sub_build_status=common_pb.INFRA_FAILURE),
      api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
      api.post_process(post_process.DoesNotRun,
                       COMPILATOR_SWARMING_TASK_COLLECT_STEP),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  fake_command_lines = {
      'browser_tests': [
          './%s' % 'browser_tests', '--fake-without-patch-flag',
          '--fake-log-file', '$ISOLATED_OUTDIR/fake.log'
      ]
  }

  def is_subsequence(containing, contained):
    containing_str = ' '.join(containing)
    contained_str = ' '.join(contained)
    return contained_str in containing_str

  yield api.test(
      'without_patch_tests_contain_command_lines_from_without_patch_compilator',
      api.chromium.try_build(
          builder_group='fake-try-group',
          builder='fake-orchestrator',
          tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
      ),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['browser_tests', 'content_unittests']),
      api.chromium_orchestrator.override_compilator_steps(
          with_patch=True, is_compile_phase=False),
      api.chromium_orchestrator.override_compilator_steps(with_patch=False),
      api.step_data('read command lines',
                    api.file.read_json(fake_command_lines)),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'content_unittests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'retry shards with patch', failures=['Test.One']),
      api.chromium_tests.gen_swarming_and_rdb_results(
          'browser_tests', 'without patch', failures=['Test.One']),
      api.post_process(post_process.MustRun,
                       'trigger compilator (without patch)'),
      api.post_process(post_process.MustRun, 'browser_tests (without patch)'),
      api.post_process(
          api.swarming.check_triggered_request,
          'test_pre_run (without patch).[trigger] '
          'browser_tests (without patch)', lambda check, req: check(
              is_subsequence(req[0].command, fake_command_lines['browser_tests']
                            ))),
      api.post_process(post_process.DropExpectation),
  )

  def _generate_test_result(test_id,
                            test_variant,
                            status=test_result_pb2.PASS,
                            tags=None):
    vh = 'variant_hash'
    tr = test_result_pb2.TestResult(
        test_id=test_id,
        variant=test_variant,
        variant_hash=vh,
        expected=False,
        status=status,
    )
    if tags:
      all_tags = getattr(tr, 'tags')
      all_tags.append(tags)
    return tr

  correct_variant = resultdb_common.Variant()
  variant_def = getattr(correct_variant, 'def')
  variant_def['os'] = 'Ubuntu-18'
  variant_def['test_suite'] = ('browser_tests')

  tags = resultdb_common.StringPair(key='test_name', value='Test:Test1')

  test_id = 'ninja://browser_tests/Test:Test1'
  inv = 'invocations/build:8945511751514863184'
  current_patchset_invocations = {
      inv:
          api.resultdb.Invocation(test_results=[
              _generate_test_result(test_id, correct_variant, tags=tags)
          ])
  }
  recent_run = test_history.QueryTestHistoryResponse(
      verdicts=[], next_page_token='dummy_token')

  def check_duplicate_instruction_ids(check, steps, update_step_name):
    check(update_step_name in steps)
    update_invocation_step = steps[update_step_name]
    check('json.input' in update_invocation_step.logs)
    ids = set()
    for instruction in api.json.loads(
        update_invocation_step.logs['json.input']
    )['invocation']['instructions']['instructions']:
      check(instruction['id'] not in ids)
      ids.add(instruction['id'])

  yield api.test(
      'new_flaky_test',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(
          affected_files=['src/chrome/test.cc', 'src/components/file2.cc']),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.resultdb.query(
          current_patchset_invocations,
          ('collect tasks (with patch).browser_tests results'),
      ),
      api.flakiness(check_for_flakiness=True),
      api.luci_analysis.query_test_history(
          recent_run,
          'ninja://browser_tests/Test:Test1',
          parent_step_name='searching_for_new_tests',
      ),
      api.override_step_data(('test new tests for flakiness.'
                              'collect tasks (check flakiness shard #0).'
                              'browser_tests results'),
                             stdout=api.json.invalid(
                                 api.test_utils.rdb_results(
                                     'browser_tests',
                                     flaky_failing_tests=['Test.One'],
                                 ))),
      api.post_process(post_process.MustRun, 'calculate flake rates'),
      api.post_process(post_process.SummaryMarkdownRE, '.*browser_tests.*'),
      api.post_process(
          post_process.MustRun,
          'test new tests for flakiness.update invocation instructions'),
      api.post_process(
          check_duplicate_instruction_ids,
          'test new tests for flakiness.update invocation instructions'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'flaky_swarming_and_local_test_failure',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['browser_tests', 'content_unittests'],
      ),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['browser_tests', 'content_unittests'],
          affected_files=['src/chrome/test.cc', 'src/components/file2.cc']),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False,
          sub_build_status=common_pb.FAILURE,
          sub_build_summary=("1 Test Suite(s) failed.\n\n"
                             "**headless_python_unittests** failed.")),
      api.resultdb.query(
          current_patchset_invocations,
          ('collect tasks (with patch).browser_tests results'),
      ),
      api.flakiness(check_for_flakiness=True),
      api.luci_analysis.query_test_history(
          recent_run,
          'ninja://browser_tests/Test:Test1',
          parent_step_name='searching_for_new_tests',
      ),
      api.override_step_data(('test new tests for flakiness.'
                              'collect tasks (check flakiness shard #0).'
                              'browser_tests results'),
                             stdout=api.json.invalid(
                                 api.test_utils.rdb_results(
                                     'browser_tests',
                                     flaky_failing_tests=['Test.One'],
                                 ))),
      api.post_process(post_process.MustRun, 'calculate flake rates'),
      api.post_process(post_process.SummaryMarkdownRE, '.*browser_tests.*'),
      api.post_process(post_process.SummaryMarkdownRE,
                       '.*headless_python_unittests.*'),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no_flaky_tests',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
          tests=['browser_tests', 'content_unittests'],
      ),
      api.chromium_orchestrator.override_compilator_steps(
          tests=['browser_tests', 'content_unittests'],
          affected_files=['src/chrome/test.cc', 'src/components/file2.cc']),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.resultdb.query(
          current_patchset_invocations,
          ('collect tasks (with patch).browser_tests results'),
      ),
      api.flakiness(check_for_flakiness=True),
      api.luci_analysis.query_test_history(
          recent_run,
          'ninja://browser_tests/Test:Test1',
          parent_step_name='searching_for_new_tests',
      ),
      api.resultdb.query(
          current_patchset_invocations,
          ('test new tests for flakiness.'
           'collect tasks (check flakiness shard #0).browser_tests results'),
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'basic_with_buildbucket_backend',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.code_coverage(use_clang_coverage=True),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(
          backend=True),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.post_process(
          post_process.StepCommandContains,
          'install infra/chromium/compilator_watcher.ensure_installed', [
              '-ensure-file', 'infra/chromium/compilator_watcher/${platform} '
              'git_revision:e841fc'
          ]),
      api.post_process(post_process.MustRun, 'set_output_gitiles_commit'),
      api.post_process(post_process.MustRun, 'trigger compilator (with patch)'),
      api.post_process(post_process.MustRun, 'browser_tests (with patch)'),
      api.post_process(post_process.MustRun,
                       'downloading cas digest all_test_binaries'),
      api.post_process(post_process.MustRun,
                       COMPILATOR_SWARMING_TASK_COLLECT_STEP),
      api.post_process(post_process.MustRun, 'ensure clang coverage tools'),
      api.post_process(post_process.MustRun, 'download src-side deps'),
      api.post_process(
          post_process.StepTextContains,
          'read test spec (fake-group.json)',
          ['[CLEANUP]/src/testing/buildbot/fake-group.json'],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'propagate-properties-to-compilator',
      get_try_build(),
      ctbc_properties(),
      api.properties(
          **{
              '$build/chromium_orchestrator':
                  InputProperties(
                      compilator='fake-compilator',
                      compilator_watcher_git_revision='e841fc',
                  ),
          }),
      api.code_coverage(use_clang_coverage=True),
      api.chromium_orchestrator.override_compilator_build_proto_fetch(),
      api.chromium_orchestrator.override_schedule_compilator_build(),
      api.chromium_orchestrator.override_compilator_steps(),
      api.chromium_orchestrator.override_compilator_steps(
          is_compile_phase=False),
      api.chromium_orchestrator.override_test_spec(
          builder_group='fake-group',
          builder='fake-builder',
          tester='fake-tester',
      ),
      api.post_process(
          post_process.LogContains, 'trigger compilator (with patch)',
          'request',
          ['$build/chromium_tests_builder_config', '$build/code_coverage']),
      api.post_process(post_process.DropExpectation),
  )
