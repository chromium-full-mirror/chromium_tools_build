# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import base64

from recipe_engine import post_process
from recipe_engine import turboci
from recipe_engine.recipe_api import RecipeApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build.chromium_tests import api as chromium_tests
from RECIPE_MODULES.build.chromium_turboci.api import ChromiumTurbociApi
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc
from RECIPE_MODULES.build.chromium_tests_builder_config import try_spec
from RECIPE_MODULES.depot_tools.tryserver import api as tryserver

from PB.go.chromium.org.luci.resultdb.proto.v1 import common as resultdb_common
from PB.go.chromium.org.luci.resultdb.proto.v1 import resultdb as rdb_pb2
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  test_result as test_result_pb2,
)
from PB.go.chromium.org.luci.analysis.proto.v1 import test_history
from PB.turboci.data.build.v1.build_check_options import (
  BuildCheckOptions,
  Product,
)
from PB.turboci.data.build.v1.build_check_results import BuildCheckResults
from PB.turboci.data.chrome.build.v1.analyze_options import AnalyzeOptions
from PB.turboci.data.chrome.build.v1.analyze_results import AnalyzeResults
from PB.turboci.data.chrome.build.v1.compile_targets_options import (
  CompileTargetsOptions,
)
from PB.turboci.data.test.v1.test_check_description_option import (
  TestCheckDescriptionOption,
)
from PB.turboci.data.test.v1.test_check_summary_result import (
  TestCheckSummaryResult,
)
from PB.turboci.graph.orchestrator.v1.check_kind import CheckKind
from PB.turboci.graph.orchestrator.v1.check_state import CheckState
from PB.turboci.graph.orchestrator.v1.workplan import WorkPlan

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_android,
  chromium_swarming,
  chromium_tests as chromium_tests_module,
  chromium_tests_builder_config,
  code_coverage,
  filter as filter_module,
  flakiness,
  orderfile,
  pgo,
  profiles,
  test_utils,
)
from RECIPE_MODULES.depot_tools import tryserver as tryserver_module
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cq,
  file,
  json,
  legacy_annotation,
  luci_analysis,
  path,
  platform,
  properties,
  raw_io,
  resultdb,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_android: chromium_android.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests_module.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  code_coverage: code_coverage.API
  cq: cq.API
  file: file.API
  filter: filter_module.API
  flakiness: flakiness.API
  json: json.API
  legacy_annotation: legacy_annotation.API
  luci_analysis: luci_analysis.API
  orderfile: orderfile.API
  path: path.API
  pgo: pgo.API
  platform: platform.API
  profiles: profiles.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  swarming: swarming.API
  test_utils: test_utils.API
  tryserver: tryserver_module.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_swarming: chromium_swarming.TEST_API
  chromium_tests: chromium_tests_module.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  code_coverage: code_coverage.TEST_API
  cq: cq.TEST_API
  file: file.TEST_API
  filter: filter_module.TEST_API
  flakiness: flakiness.TEST_API
  json: json.TEST_API
  luci_analysis: luci_analysis.TEST_API
  orderfile: orderfile.TEST_API
  path: path.TEST_API
  pgo: pgo.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  resultdb: resultdb.TEST_API
  swarming: swarming.TEST_API
  test_utils: test_utils.TEST_API
  tryserver: tryserver_module.TEST_API


_TEST_BUILDERS = ctbc.BuilderDatabase.create(
  {
    'chromium.test': {
      'chromium-rel': ctbc.BuilderSpec.create(
        chromium_config='chromium',
        gclient_config='chromium',
      ),
      'retry-shards': ctbc.BuilderSpec.create(
        chromium_config='chromium',
        gclient_config='chromium',
      ),
      'retry-shards-test': ctbc.BuilderSpec.create(
        execution_mode=ctbc.TEST,
        parent_buildername='retry-shards',
      ),
    },
    'tryserver.chromium.unmirrored': {
      'unmirrored-chromium-rel': ctbc.BuilderSpec.create(
        chromium_config='chromium',
        gclient_config='chromium',
      ),
    },
  }
)

_TEST_TRYBOTS = ctbc.TryDatabase.create(
  {
    'tryserver.chromium.test': {
      'disable-retry-wo-patch': ctbc.TrySpec.create(
        retry_failed_shards=True,
        retry_without_patch=False,
        mirrors=[
          ctbc.TryMirror.create(
            builder_group='chromium.test',
            buildername='retry-shards',
            tester='retry-shards-test',
          ),
        ],
      ),
      'retry-shards': ctbc.TrySpec.create(
        retry_failed_shards=True,
        mirrors=[
          ctbc.TryMirror.create(
            builder_group='chromium.test',
            buildername='retry-shards',
            tester='retry-shards-test',
          ),
        ],
      ),
    }
  }
)


def RunSteps(api: RecipeApi):
  assert api.tryserver.is_tryserver
  api.path.mock_add_paths(
    api.profiles.profile_dir().joinpath('overall-merged.profdata')
  )
  api.path.mock_add_paths(
    api.profiles.profile_dir().joinpath(api.pgo.TEMP_PROFDATA_FILENAME)
  )
  api.path.mock_add_paths(api.profiles.profile_dir().joinpath('orderfile.out'))

  api.chromium_swarming.set_default_dimension('os', 'Linux')
  builder_id, builder_config = (
    api.chromium_tests_builder_config.lookup_builder()
  )
  raw_result = api.chromium_tests.trybot_steps(builder_id, builder_config)
  return raw_result


def GenTests(api: RecipeTestApi):
  ctbc_api = api.chromium_tests_builder_config

  def basic_assert_workplan(assert_, workplan: WorkPlan):
    analyze_check_id = f'{chromium_tests.BUILD_CHECK_ID} analyze'
    check_ids = set(c.identifier.id for c in workplan.checks)
    if not assert_(
      check_ids
      == {
        chromium_tests.SOURCE_CHECK_ID,
        chromium_tests.BUILD_CHECK_ID,
        analyze_check_id,
      }
    ):
      return  # pragma: no cover

    # source check verifications ###############################################
    # The source check is created by bot_update, just verify it's of appropriate
    # kind and is final, none of the other details since the check isn't being
    # read by the recipe code
    source_check = turboci.get_check_by_short_id(
      workplan, chromium_tests.SOURCE_CHECK_ID
    )
    assert_(source_check.kind == CheckKind.CHECK_KIND_SOURCE)
    assert_(source_check.state == CheckState.CHECK_STATE_FINAL)

    # build check verifications ################################################
    build_check = turboci.get_check_by_short_id(
      workplan, chromium_tests.BUILD_CHECK_ID
    )
    assert_(build_check.kind == CheckKind.CHECK_KIND_BUILD)
    assert_(build_check.state == CheckState.CHECK_STATE_FINAL)

    # Verify that the build check depends on the source and analyze checks
    build_check_deps = {
      edge.check.identifier.id for edge in build_check.dependencies.edges
    }
    assert_(
      build_check_deps == {chromium_tests.SOURCE_CHECK_ID, analyze_check_id}
    )

    # Verify the options on the build check
    build_check_options = turboci.get_option(BuildCheckOptions, build_check)
    expected_build_check_options = BuildCheckOptions(
      target=BuildCheckOptions.BuildTarget(
        name='chromium/try/fake-try-builder',
        product=Product.PRODUCT_BROWSER,
      )
    )
    assert_(build_check_options == expected_build_check_options)

    compile_targets_options = turboci.get_option(
      CompileTargetsOptions, build_check
    )
    expected_compile_targets_options = CompileTargetsOptions(
      compile_targets=['base_unittests', 'foo']
    )
    assert_(compile_targets_options == expected_compile_targets_options)

    # Verify the results on the build check
    build_check_results = turboci.get_results(BuildCheckResults, build_check)
    expected_build_check_results = [BuildCheckResults(success=True)]
    assert_(build_check_results == expected_build_check_results)

    # analyze check verifications ##############################################
    analyze_check = turboci.get_check_by_short_id(workplan, analyze_check_id)
    assert_(analyze_check.kind == CheckKind.CHECK_KIND_ANALYSIS)
    assert_(analyze_check.state == CheckState.CHECK_STATE_FINAL)

    # Verify the options on the analyze check
    analyze_options = turboci.get_option(AnalyzeOptions, analyze_check)
    expected_analyze_options = AnalyzeOptions(
      compile_targets=['foo'],
      test_targets=['base_unittests'],
      analyze_config_path='testing/buildbot/trybot_analyze_config.json',
      analyze_config_names=['chromium', 'linux'],
    )
    assert_(analyze_options == expected_analyze_options)

    # Verify the results on the analyze check
    analyze_results = turboci.get_results(AnalyzeResults, analyze_check)
    expected_analyze_results = [
      AnalyzeResults(
        compile_targets=['base_unittests', 'foo'],
        test_targets=['base_unittests'],
      ),
    ]
    assert_(analyze_results == expected_analyze_results)

  yield api.test(
    'basic',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'additional_compile_targets': ['foo'],
          'gtest_tests': [
            {
              'test': 'base_unittests',
            }
          ],
        },
      },
    ),
    api.post_process(post_process.StepSuccess, 'gerrit fetch current CL info'),
    api.post_process(
      post_process.StepCommandContains,
      'bot_update',
      [
        '--refs',
        'refs/heads/main',
      ],
    ),
    api.post_process(post_process.StepSuccess, 'gclient runhooks (with patch)'),
    api.post_process(post_process.StepSuccess, 'compile (with patch)'),
    api.post_process(post_process.StepSuccess, 'base_unittests (with patch)'),
    api.post_process(post_process.DropExpectation),
    api.assert_workplan(basic_assert_workplan),
  )

  yield api.test(
    'basic-branch',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
            }
          ],
        },
      },
    ),
    api.properties(root_solution_revision='refs/branch-heads/4472'),
    api.post_process(post_process.StepSuccess, 'gerrit fetch current CL info'),
    api.post_process(
      post_process.StepCommandContains,
      'bot_update',
      [
        '--refs',
        'refs/branch-heads/4472',
      ],
    ),
    api.post_process(post_process.StepSuccess, 'gclient runhooks (with patch)'),
    api.post_process(post_process.StepSuccess, 'compile (with patch)'),
    api.post_process(post_process.StepSuccess, 'base_unittests (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'gtest_multiple_filters',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {},
              'args': [
                '--test-launcher-filter-file=filter1',
                '--test-launcher-filter-file=filter2',
              ],
            }
          ],
        },
      },
    ),
    api.step_data(
      'find command lines (with patch)',
      api.json.output(
        {
          'base_unittests': [
            './base_unittests',
            '--test-launcher-filter-file=filter3',
          ]
        }
      ),
    ),
    api.post_check(
      api.swarming.check_triggered_request,
      'test_pre_run (with patch).[trigger] base_unittests (with patch)',
      lambda check, req: check(
        '--test-launcher-filter-file=filter1;filter2;filter3' in req[0].command
      ),
    ),
    api.post_process(post_process.StepSuccess, 'gerrit fetch current CL info'),
    api.post_process(
      post_process.StepCommandContains,
      'bot_update',
      [
        '--refs',
        'refs/heads/main',
      ],
    ),
    api.post_process(post_process.StepSuccess, 'gclient runhooks (with patch)'),
    api.post_process(post_process.StepSuccess, 'compile (with patch)'),
    api.post_process(post_process.StepSuccess, 'base_unittests (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'analyze_compile_mode',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group', builder='fake-try-builder'
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
        builder_spec=ctbc.BuilderSpec.create(
          gclient_config='chromium',
          chromium_config='chromium',
          chromium_apply_config=['clobber'],
        ),
      )
      .assemble()
    ),
    api.post_process(post_process.StepSuccess, 'clobber'),
    api.post_process(post_process.StepSuccess, 'analyze'),
    api.post_process(post_process.DoesNotRunRE, 'compile.*'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_compile',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.post_process(post_process.DoesNotRun, 'clobber'),
    api.post_process(post_process.StepSuccess, 'analyze'),
    api.post_process(
      post_process.StepCommandContains,
      'analyze',
      [
        '{"additional_compile_targets": [], '
        '"files": ["foo.cc"], '
        '"test_targets": []}',
      ],
    ),
    api.post_process(post_process.DoesNotRunRE, 'compile.*'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_compile_no_source',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.override_step_data(
      'git diff to analyze patch', api.raw_io.stream_output('OWNERS')
    ),
    api.post_process(post_process.DoesNotRun, 'clobber'),
    api.post_process(post_process.StepSuccess, 'analyze'),
    api.post_process(
      post_process.StepCommandContains,
      'analyze',
      [
        '{"additional_compile_targets": [], '
        '"files": ["OWNERS"], '
        '"test_targets": []}',
      ],
    ),
    api.post_process(post_process.DoesNotRunRE, 'compile.*'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'unmirrored',
    api.chromium_tests_builder_config.try_build(
      builder_group='tryserver.chromium.unmirrored',
      builder='unmirrored-chromium-rel',
      builder_db=_TEST_BUILDERS,
      try_db=None,
    ),
    api.chromium_tests.read_targets_spec(
      'tryserver.chromium.unmirrored',
      {
        'unmirrored-chromium-rel': {
          'gtest_tests': [
            {
              'test': 'bogus_unittests',
            }
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'bogus_unittests (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  def custom_props():
    return sum(
      [
        api.chromium_tests_builder_config.try_build(
          builder_group='tryserver.chromium.test',
          builder='retry-shards',
          builder_db=_TEST_BUILDERS,
          try_db=_TEST_TRYBOTS,
        ),
        api.properties(
          swarm_hashes={
            'base_unittests': '[dummy hash for base_unittests/size]'
          },
        ),
      ],
      api.empty_test_data(),
    )

  yield api.test(
    'retry_shards',
    custom_props(),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'retry-shards': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.override_step_data(
      'base_unittests (with patch)',
      api.chromium_swarming.canned_summary_output(
        api.test_utils.canned_gtest_output(False), failure=True
      ),
    ),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(post_process.DoesNotRun, 'base_unittests (without patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'infra_fail_retry_test_failure_is_not_infra_failure',
    custom_props(),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'retry-shards': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.override_step_data(
      'base_unittests (with patch)',
      api.chromium_swarming.canned_summary_output(
        api.test_utils.canned_gtest_output(False), internal_failure=True
      ),
    ),
    api.override_step_data(
      'base_unittests (retry shards with patch)',
      api.chromium_swarming.canned_summary_output(
        api.test_utils.canned_gtest_output(False), failure=True
      ),
    ),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    # The shard did not infra fail on retry and should be treated as a normal
    # failure, not infra failure
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  def retry_shards_without_patch_assert_workplan(assert_, workplan: WorkPlan):
    analyze_check_id = f'{chromium_tests.BUILD_CHECK_ID} analyze'
    test_check_ids = (
      ChromiumTurbociApi.get_test_check_id('base_unittests (with patch)'),
      ChromiumTurbociApi.get_test_check_id(
        'base_unittests (retry shards with patch)'
      ),
      ChromiumTurbociApi.get_test_check_id('base_unittests (without patch)'),
    )
    check_ids = set(c.identifier.id for c in workplan.checks)
    if not assert_(
      check_ids
      == {
        chromium_tests.SOURCE_CHECK_ID,
        chromium_tests.BUILD_CHECK_ID,
        analyze_check_id,
        chromium_tests.WITHOUT_PATCH_SOURCE_CHECK_ID,
        chromium_tests.WITHOUT_PATCH_BUILD_CHECK_ID,
      }
      ^ set(test_check_ids)
    ):
      return  # pragma: no cover

    # The basic test case already verifies the source, build and analyze checks
    # with the patch applied, just verify the without patch checks

    # without patch source check verifications #################################
    # The source check is created by bot_update, just verify it's of appropriate
    # kind and is final, none of the other details since the check isn't being
    # read by the recipe code
    source_check = turboci.get_check_by_short_id(
      workplan, chromium_tests.WITHOUT_PATCH_SOURCE_CHECK_ID
    )
    assert_(source_check.kind == CheckKind.CHECK_KIND_SOURCE)
    assert_(source_check.state == CheckState.CHECK_STATE_FINAL)

    # without patch build check verifications ##################################
    build_check = turboci.get_check_by_short_id(
      workplan, chromium_tests.WITHOUT_PATCH_BUILD_CHECK_ID
    )
    assert_(build_check.kind == CheckKind.CHECK_KIND_BUILD)
    assert_(build_check.state == CheckState.CHECK_STATE_FINAL)

    # Verify that the without patch build check depends on the without patch
    # source check
    build_check_deps = {
      edge.check.identifier.id for edge in build_check.dependencies.edges
    }
    assert_(build_check_deps == {chromium_tests.WITHOUT_PATCH_SOURCE_CHECK_ID})

    # Verify the options on the without patch build check
    build_check_options = turboci.get_option(BuildCheckOptions, build_check)
    expected_build_check_options = BuildCheckOptions(
      target=BuildCheckOptions.BuildTarget(
        name='chromium/try/retry-shards',
        product=Product.PRODUCT_BROWSER,
      )
    )
    assert_(build_check_options == expected_build_check_options)

    compile_targets_options = turboci.get_option(
      CompileTargetsOptions, build_check
    )
    expected_compile_targets_options = CompileTargetsOptions(
      compile_targets=['base_unittests']
    )
    assert_(compile_targets_options == expected_compile_targets_options)

    # Verify the results on the without patch build check
    build_check_results = turboci.get_results(BuildCheckResults, build_check)
    expected_build_check_results = [BuildCheckResults(success=True)]
    assert_(build_check_results == expected_build_check_results)

    # Verify the results on the test check
    for test_check_id in test_check_ids:
      test_check = turboci.get_check_by_short_id(workplan, test_check_id)
      test_check_result = turboci.get_results(
        TestCheckSummaryResult, test_check
      )[0]
      # 'Test.One' keeps failing in base_unittests in all phases
      assert_(not test_check_result.success)
      assert_('Test.One' in test_check_result.display_message.message)

  yield api.test(
    'retry_shards_without_patch',
    custom_props(),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'retry-shards': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'with patch', failures=['Test.One']
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'retry shards with patch', failures=['Test.One']
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'without patch', failures=['Test.One']
    ),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(post_process.MustRun, 'base_unittests (without patch)'),
    api.post_process(post_process.DropExpectation),
    api.assert_workplan(retry_shards_without_patch_assert_workplan),
  )

  yield api.test(
    'without_patch_notrun_failure',
    custom_props(),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'retry-shards': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'with patch', failures=['Test.One']
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'retry shards with patch', failures=['Test.One']
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'without patch', skips=['Test.One']
    ),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(post_process.MustRun, 'base_unittests (without patch)'),
    # A test that exits with FAILURE in 'with patch' then NOTRUN in
    # 'without patch' should fail the build.
    api.expect_status('FAILURE'),
    api.post_process(post_process.MustRun, 'record test suite statuses'),
    api.post_process(
      post_process.PropertyEquals, 'test_status', {'base_unittests': 'Failure'}
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'retry_shards_invalid',
    api.chromium_tests_builder_config.try_build(
      builder_group='tryserver.chromium.test',
      builder='retry-shards',
      builder_db=_TEST_BUILDERS,
      try_db=_TEST_TRYBOTS,
    ),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'retry-shards': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.override_step_data(
      'base_unittests (with patch)',
      api.chromium_swarming.canned_summary_output(
        api.test_utils.gtest_results('invalid_results', 1), failure=True
      ),
    ),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(post_process.DoesNotRun, 'base_unittests (without patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'skips_successful_tests',
    api.chromium_tests_builder_config.try_build(
      builder_group='tryserver.chromium.test',
      builder='retry-shards',
      builder_db=_TEST_BUILDERS,
      try_db=_TEST_TRYBOTS,
      tags=api.buildbucket.tags(
        cq_equivalent_cl_group_key='12345', cq_attempt_key='67890'
      ),
    ),
    api.cq(run_mode='FULL_RUN'),
    api.properties(
      swarm_hashes={
        'browser_tests': '[dummy hash for base_unittests/size]',
        'unit_tests': '[dummy hash for base_unittests/size]',
      }
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'retry-shards': {
          'gtest_tests': [
            {
              'name': 'browser_tests',
              'test': 'browser_tests',
              'swarming': {},
            },
            {
              'name': 'unit_tests',
              'test': 'unit_tests',
              'swarming': {},
            },
          ],
        },
      },
    ),
    api.chromium_tests.simulate_previous_build(
      test_statuses={'unit_tests': 'Success'}
    ),
    api.post_process(post_process.MustRun, 'unit_tests (with patch)'),
    api.post_process(
      post_process.StepTextEquals,
      'unit_tests (with patch)',
      (
        'This test is not being run because it has passed in the last 24 '
        'hours with the equivalent patchset'
      ),
    ),
    api.post_process(
      post_process.StepCommandDoesNotContain,
      'compile (with patch)',
      ['unit_tests'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'compile (with patch)',
      ['browser_tests'],
    ),
    api.post_process(
      post_process.LogDoesNotContain,
      'isolate tests (with patch)',
      'json.output',
      ['unit_tests'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'retry_shards_invalid_retry',
    api.chromium_tests_builder_config.try_build(
      builder_group='tryserver.chromium.test',
      builder='retry-shards',
      builder_db=_TEST_BUILDERS,
      try_db=_TEST_TRYBOTS,
    ),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'retry-shards': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'with patch', failures=['Test.One']
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'retry shards with patch', failures=['Test.One']
    ),
    api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(post_process.MustRun, 'base_unittests (without patch)'),
    api.expect_status('FAILURE'),
    api.post_process(post_process.MustRun, 'record test suite statuses'),
    api.post_process(
      post_process.PropertyEquals, 'test_status', {'base_unittests': 'Failure'}
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'retry_shards_all_invalid_results',
    api.chromium_tests_builder_config.try_build(
      builder_group='tryserver.chromium.test',
      builder='retry-shards',
      builder_db=_TEST_BUILDERS,
      try_db=_TEST_TRYBOTS,
    ),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'retry-shards': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.override_step_data(
      'base_unittests (with patch)',
      api.chromium_swarming.canned_summary_output(
        api.test_utils.gtest_results('invalid results', 1), failure=True
      ),
    ),
    api.override_step_data(
      'base_unittests (retry shards with patch)',
      api.chromium_swarming.canned_summary_output(
        api.test_utils.gtest_results('invalid results', 1), failure=True
      ),
    ),
    api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(post_process.DoesNotRun, 'base_unittests (without patch)'),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'swarming_test_has_valid_results_mixed_shards',
    custom_props(),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'retry-shards': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {'shards': 2},
            }
          ],
        },
      },
    ),
    api.override_step_data(
      'base_unittests (with patch)',
      api.chromium_swarming.summary(
        api.test_utils.canned_gtest_output(False),
        api.chromium_swarming.canned_summary_output_raw(
          shards=2,
          task_ids=['0', '1'],
          invocations=['invocations/0', 'invocations/1'],
          failure=True,
        ),
        retcode=1,
      ),
    ),
    api.resultdb.query(
      {
        'invocations/0': api.resultdb.Invocation(
          test_results=[
            test_result_pb2.TestResult(
              test_id='Test.One', status=test_result_pb2.FAIL
            )
          ]
        ),
        'invocations/1': api.resultdb.Invocation(test_results=[]),
      },
      step_name=('collect tasks (with patch).base_unittests results'),
    ),
    api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(
      post_process.MustRun,
      (
        'test_pre_run (retry shards with patch).'
        '[trigger] base_unittests (retry shards with patch)'
      ),
    ),
    api.post_process(
      post_process.MustRun,
      (
        'test_pre_run (retry shards with patch).'
        '[trigger] base_unittests (retry shards with patch) (2)'
      ),
    ),
    api.post_process(
      post_process.DoesNotRun,
      (
        'test_pre_run (retry shards with patch).'
        '[trigger] base_unittests (retry shards with patch) (3)'
      ),
    ),
    api.post_process(post_process.DoesNotRun, 'base_unittests (without patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'swarming_test_has_invalid_shards',
    custom_props(),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {  #  custom_props sets builder group to 'tryserver.chromium.test'
        'retry-shards': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {'shards': 2},
            }
          ],
        },
      },
    ),
    api.override_step_data(
      'base_unittests (with patch)',
      api.chromium_swarming.summary(
        api.test_utils.canned_gtest_output(False),
        api.chromium_swarming.canned_summary_output_raw(
          shards=2,
          task_ids=['0', '1'],
          invocations=['invocations/0', 'invocations/1'],
          failure=True,
        ),
        retcode=1,
      ),
    ),
    api.resultdb.query(
      {
        'invocations/0': api.resultdb.Invocation(test_results=[]),
        'invocations/1': api.resultdb.Invocation(test_results=[]),
      },
      step_name=('collect tasks (with patch).base_unittests results'),
    ),
    api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(
      post_process.MustRun,
      (
        'test_pre_run (retry shards with patch).'
        '[trigger] base_unittests (retry shards with patch)'
      ),
    ),
    api.post_process(
      post_process.MustRun,
      (
        'test_pre_run (retry shards with patch).'
        '[trigger] base_unittests (retry shards with patch) (2)'
      ),
    ),
    api.post_process(
      post_process.DoesNotRun,
      (
        'test_pre_run (retry shards with patch).'
        '[trigger] base_unittests (retry shards with patch) (3)'
      ),
    ),
    api.post_process(post_process.DoesNotRun, 'base_unittests (without patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'disable_retry_without_patch',
    api.chromium_tests_builder_config.try_build(
      builder_group='tryserver.chromium.test',
      builder='disable-retry-wo-patch',
      builder_db=_TEST_BUILDERS,
      try_db=_TEST_TRYBOTS,
    ),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'retry-shards': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'with patch', failures=['Test.One']
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'retry shards with patch', failures=['Test.One']
    ),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(post_process.DoesNotRun, 'base_unittests (without patch)'),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'code_coverage_trybot_with_patch',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
            }
          ],
        },
      },
    ),
    api.code_coverage(use_clang_coverage=True),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'isolate_profile_data': True,
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
    api.post_process(
      post_process.DoesNotRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(post_process.DoesNotRun, 'base_unittests (without patch)'),
    api.post_process(
      # Only generates coverage data for the with patch step.
      post_process.MustRun,
      'process clang code coverage data for overall test coverage.generate '
      'metadata for overall test coverage in 1 tests',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'code_coverage_trybot_retry_shards_with_patch',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
            }
          ],
        },
      },
    ),
    api.code_coverage(use_clang_coverage=True),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'isolate_profile_data': True,
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.override_step_data(
      'base_unittests (with patch)',
      api.chromium_swarming.canned_summary_output(
        api.test_utils.canned_gtest_output(False), failure=True
      ),
    ),
    api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(post_process.DoesNotRun, 'base_unittests (without patch)'),
    api.post_process(
      # Generates coverage data for the with patch and retry shards with
      # patch steps.
      post_process.MustRun,
      'process clang code coverage data for overall test coverage.generate '
      'metadata for overall test coverage in 2 tests',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'code_coverage_trybot_without_patch',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
            }
          ],
        },
      },
    ),
    api.code_coverage(use_clang_coverage=True),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'isolate_profile_data': True,
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'with patch', failures=['Test.One']
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'retry shards with patch', failures=['Test.One']
    ),
    api.post_process(post_process.MustRun, 'base_unittests (with patch)'),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(post_process.MustRun, 'base_unittests (without patch)'),
    api.post_process(
      # Generates coverage data for the with patch and retry shards with
      # patch steps. Without patch steps are always ignored.
      post_process.MustRun,
      'process clang code coverage data for overall test coverage.generate '
      'metadata for overall test coverage in 2 tests',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'orderfile_trybot',
    api.chromium_tests_builder_config.try_build(
      builder_group='orderfile-try-group',
      builder='orderfile-try-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'orderfile-group': {
            'orderfile-builder': ctbc.BuilderSpec.create(
              android_config='base_config',
              chromium_config='android',
              chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_ARCH': 'arm',
                'TARGET_BITS': 64,
                'TARGET_PLATFORM': 'android',
              },
              gclient_config='chromium',
              gclient_apply_config=['android'],
              simulation_platform='linux',
            ),
          },
        }
      ),
      try_db=ctbc.TryDatabase.create(
        {
          'orderfile-try-group': {
            'orderfile-try-builder': ctbc.TrySpec.create_for_single_mirror(
              'orderfile-group', 'orderfile-builder'
            ),
          },
        }
      ),
    ),
    api.properties(
      swarm_hashes={
        'performance_test_suite': '[dummy hash for performance_test_suite/size]'
      },
    ),
    api.orderfile(use_orderfile=True),
    api.platform('linux', 64),
    api.chromium_tests.read_targets_spec(
      'orderfile-group',
      {
        'orderfile-builder': {
          'isolated_scripts': [
            {
              'name': 'performance_test_suite',
              'isolate_profile_data': True,
              'test': 'performance_test_suite',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'processing generated orderfile'),
    api.post_process(post_process.MustRunRE, '.*skipping upload to CIPD.*'),
    api.post_process(
      post_process.DoesNotRunRE, '.*uploading generated orderfile to CIPD'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'pgo_trybot',
    api.chromium_tests_builder_config.try_build(
      builder_group='pgo-try-group',
      builder='pgo-try-builder',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'pgo-group': {
            'pgo-builder': ctbc.BuilderSpec.create(
              android_config='base_config',
              chromium_config='android',
              chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
                'TARGET_PLATFORM': 'android',
              },
              gclient_config='chromium',
              gclient_apply_config=['android'],
              simulation_platform='linux',
            ),
          },
        }
      ),
      try_db=ctbc.TryDatabase.create(
        {
          'pgo-try-group': {
            'pgo-try-builder': ctbc.TrySpec.create_for_single_mirror(
              'pgo-group', 'pgo-builder'
            ),
          },
        }
      ),
    ),
    api.properties(
      swarm_hashes={
        'performance_test_suite': '[dummy hash for performance_test_suite/size]'
      },
    ),
    api.pgo(use_pgo=True, skip_profile_upload=True),
    api.platform('linux', 64),
    api.chromium_tests.read_targets_spec(
      'pgo-group',
      {
        'pgo-builder': {
          'isolated_scripts': [
            {
              'name': 'performance_test_suite',
              'isolate_profile_data': True,
              'test': 'performance_test_suite',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.post_process(
      post_process.DoesNotRunRE, '.*gsutil upload artifact to GS.*'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'pgo_trybot_uploads',
    api.chromium_tests_builder_config.try_build(
      builder_group='pgo-try-group',
      builder='pgo-try-builder',
      revision='a' * 40,
      builder_db=ctbc.BuilderDatabase.create(
        {
          'pgo-group': {
            'pgo-builder': ctbc.BuilderSpec.create(
              android_config='base_config',
              chromium_config='android',
              chromium_config_kwargs={
                'BUILD_CONFIG': 'Release',
                'TARGET_BITS': 32,
                'TARGET_PLATFORM': 'android',
              },
              gclient_config='chromium',
              gclient_apply_config=['android'],
              simulation_platform='linux',
            ),
          },
        }
      ),
      try_db=ctbc.TryDatabase.create(
        {
          'pgo-try-group': {
            'pgo-try-builder': ctbc.TrySpec.create_for_single_mirror(
              'pgo-group', 'pgo-builder'
            ),
          },
        }
      ),
    ),
    api.properties(
      swarm_hashes={
        'performance_test_suite': '[dummy hash for performance_test_suite/size]'
      },
    ),
    api.pgo(use_pgo=True),
    api.platform('linux', 64),
    api.chromium_tests.read_targets_spec(
      'pgo-group',
      {
        'pgo-builder': {
          'isolated_scripts': [
            {
              'name': 'performance_test_suite',
              'isolate_profile_data': True,
              'test': 'performance_test_suite',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.override_step_data(
      'validate benchmark results and profile data.searching for '
      'profdata files',
      api.file.listdir(
        [('/performance_test_suite_with_patch/performance_test_suite.profdata')]
      ),
    ),
    api.post_process(
      post_process.MustRunRE, '.*gsutil upload artifact to GS.*'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'many_invalid_results',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
            }
          ],
        },
      },
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'base_unittests1',
            },
            {
              'test': 'base_unittests2',
            },
            {
              'test': 'base_unittests3',
            },
          ],
        },
      },
    ),
    api.chromium_tests.change_size_limit(2),
    api.override_step_data(
      'base_unittests1 results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results(
          'base_unittests1', failing_tests=['Test.One']
        )
      ),
    ),
    api.override_step_data(
      'base_unittests2 results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results(
          'base_unittests2', failing_tests=['Test.One']
        )
      ),
    ),
    api.override_step_data(
      'base_unittests3 results',
      stdout=api.raw_io.output_text(
        api.test_utils.rdb_results(
          'base_unittests3', failing_tests=['Test.One']
        )
      ),
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.SummaryMarkdownRE, r'3 Test Suite\(s\) failed.*'
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'basic dryrun',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.properties(dry_run=True),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
            }
          ],
        },
      },
    ),
    api.post_process(post_process.StepSuccess, 'gerrit fetch current CL info'),
    api.post_process(
      post_process.StepCommandContains,
      'bot_update',
      [
        '--refs',
        'refs/heads/main',
      ],
    ),
    api.post_process(post_process.StepSuccess, 'gclient runhooks (with patch)'),
    api.post_process(post_process.StepSuccess, 'compile (with patch)'),
    api.post_process(post_process.StepSuccess, 'base_unittests (with patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'full run rts',
    api.properties(
      **{
        "$recipe_engine/cv": {
          "active": True,
          "dryRun": True,
          "runMode": "FULL_RUN",
          "topLevel": True,
        }
      }
    ),
    api.chromium.try_build(
      builder_group='tryserver.chromium.test',
      builder='fake-try-builder',
      experiments=['chromium_rts.filter_file_analysis'],
      tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='chromium.test',
        builder='chromium-rel',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'chromium-rel': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.post_process(post_process.MustRun, 'Evaluate chromium-rts safety'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'full run enabled but not used',
    api.properties(
      **{
        "$recipe_engine/cv": {
          "active": True,
          "dryRun": True,
          "runMode": "FULL_RUN",
          "topLevel": True,
        }
      }
    ),
    api.chromium.try_build(
      builder_group='tryserver.chromium.test',
      builder='fake-try-builder',
      experiments=['chromium_rts.filter_file_analysis'],
      tags=api.buildbucket.tags(cq_attempt_key='fake-cq-attempt-key'),
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='chromium.test',
        builder='chromium-rel',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'chromium-rel': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.post_process(post_process.DoesNotRun, 'RTS was used'),
    api.post_process(post_process.PropertiesDoNotContain, 'rts_was_used'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'depend_on_footer_failure',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.step_data(
      'parse description',
      api.json.output(
        {tryserver.constants.CQ_DEPEND_FOOTER: 'chromium:123456'}
      ),
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.SummaryMarkdownRE,
      r'Commit message footer Cq-Depend is not supported.*',
    ),
    api.post_process(post_process.DropExpectation),
  )

  builder_db = ctbc.BuilderDatabase.create(
    {
      'fake-group': {
        'fake-builder': ctbc.BuilderSpec.create(
          chromium_config='chromium',
          gclient_config='chromium',
        ),
      },
      'fake-tester-group': {
        'fake-tester': ctbc.BuilderSpec.create(
          execution_mode=ctbc.TEST,
          parent_builder_group='fake-group',
          parent_buildername='fake-builder',
          chromium_config='chromium',
          gclient_config='chromium',
        ),
      },
      'fake-unmirrored-tester-group': {
        'fake-unmirrored-tester': ctbc.BuilderSpec.create(
          execution_mode=ctbc.TEST,
          parent_builder_group='fake-group',
          parent_buildername='fake-builder',
          chromium_config='chromium',
          gclient_config='chromium',
        ),
      },
    }
  )

  yield api.test(
    'unmirrored-tester',
    api.chromium_tests_builder_config.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
      builder_db=builder_db,
      try_db=ctbc.TryDatabase.create(
        {
          'fake-try-group': {
            'fake-try-builder': ctbc.TrySpec.create_for_single_mirror(
              builder_group='fake-group',
              buildername='fake-builder',
              tester_group='fake-tester-group',
              tester='fake-tester',
            ),
          },
        }
      ),
    ),
    api.post_check(
      post_process.MustRun, 'read test spec (fake-tester-group.json)'
    ),
    api.post_check(
      post_process.DoesNotRun,
      'read test spec (fake-unmirrored-tester-group.json)',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'unmirrored-tester-include-all-triggered-testers',
    api.chromium_tests_builder_config.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
      builder_db=builder_db,
      try_db=ctbc.TryDatabase.create(
        {
          'fake-try-group': {
            'fake-try-builder': ctbc.TrySpec.create_for_single_mirror(
              builder_group='fake-group',
              buildername='fake-builder',
              include_all_triggered_testers=True,
            ),
          },
        }
      ),
    ),
    api.post_check(
      post_process.MustRun, 'read test spec (fake-tester-group.json)'
    ),
    api.post_check(
      post_process.MustRun, 'read test spec (fake-unmirrored-tester-group.json)'
    ),
    api.post_process(post_process.DropExpectation),
  )

  def _generate_test_result(test_id, test_variant, status=None):
    status = status or test_result_pb2.PASS
    vd = getattr(test_variant, 'def')
    vh = base64.b64encode(
      ('\n'.join('{}:{}'.format(k, v) for k, v in vd.items())).encode('utf-8')
    ).decode('utf-8')
    return test_result_pb2.TestResult(
      test_id=test_id,
      variant=test_variant,
      variant_hash=vh,
      expected=False,
      status=status,
    )

  correct_variant = resultdb_common.Variant()
  variant_def = getattr(correct_variant, 'def')
  variant_def['os'] = 'Mac-11'
  variant_def['test_suite'] = (
    'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4'
  )

  test_id = (
    'ninja://ios/chrome/test/earl_grey2:ios_chrome_bookmarks_eg2tests_module/'
    'TestSuite.test_a'
  )
  inv = 'invocations/1'
  current_patchset_invocations = {
    inv: api.resultdb.Invocation(
      test_results=[_generate_test_result(test_id, correct_variant)]
    )
  }

  yield api.test(
    'basic_flakiness',
    api.chromium_tests_builder_config.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
      builder_db=builder_db,
      try_db=ctbc.TryDatabase.create(
        {
          'fake-try-group': {
            'fake-try-builder': ctbc.TrySpec.create_for_single_mirror(
              builder_group='fake-group',
              buildername='fake-builder',
              include_all_triggered_testers=True,
            ),
          },
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              "test": "ios_chrome_bookmarks_eg2tests_module",
              "name": ("ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4"),
              "swarming": {
                "dimensions": {"os": "Mac-11"},
              },
              "test_id_prefix": (
                "ninja://ios/chrome/test/earl_grey2:"
                "ios_chrome_bookmarks_eg2tests_module/"
              ),
            },
            {
              "test": "ci_only_test",
              "ci_only": True,
            },
          ],
        },
      },
    ),
    api.flakiness(
      check_for_flakiness=True,
    ),
    # This overrides the file check to ensure that we have test files
    # in the given patch.
    api.step_data(
      'git diff to analyze patch (2)',
      api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc'),
    ),
    api.resultdb.query(
      inv_bundle=current_patchset_invocations,
      step_name=(
        'collect tasks (with patch).'
        'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'
      ),
    ),
    api.flakiness(check_for_flakiness=True),
    api.resultdb.query_new_test_variants(
      rdb_pb2.QueryNewTestVariantsResponse(
        is_baseline_ready=True,
        new_test_variants=[
          rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
            test_id=(
              'ninja://ios/chrome/test/earl_grey2:'
              'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_a'
            ),
            variant_hash=(
              'b3M6TWFjLTExCnRlc3Rfc3VpdGU6aW9zX2Nocm9tZV9ib29r'
              'bWFya3NfZWcydGVzdHNfbW9kdWxlX2lQYWQgQWlyIDIgMTQuNA=='
            ),
          )
        ],
      ),
      step_name='searching_for_new_tests with ResultDB.query_new_test_variants',
    ),
    api.resultdb.query(
      inv_bundle=current_patchset_invocations,
      step_name=(
        'test new tests for flakiness.'
        'collect tasks (check flakiness shard #0).'
        'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  flaky_results = {
    'invocations/100': api.resultdb.Invocation(
      test_results=[
        _generate_test_result(test_id, correct_variant),
        _generate_test_result(
          test_id, correct_variant, status=test_result_pb2.FAIL
        ),
      ]
    )
  }

  yield api.test(
    'failed_test',
    api.chromium_tests_builder_config.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
      builder_db=builder_db,
      try_db=ctbc.TryDatabase.create(
        {
          'fake-try-group': {
            'fake-try-builder': ctbc.TrySpec.create_for_single_mirror(
              builder_group='fake-group',
              buildername='fake-builder',
            ),
          },
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              "test": "ios_chrome_bookmarks_eg2tests_module",
              "name": ("ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4"),
              "swarming": {
                "dimensions": {"os": "Mac-11"},
              },
              "test_id_prefix": (
                "ninja://ios/chrome/test/earl_grey2:"
                "ios_chrome_bookmarks_eg2tests_module/"
              ),
            },
          ],
        },
      },
    ),
    api.flakiness(
      check_for_flakiness=True,
    ),
    # This overrides the file check to ensure that we have test files
    # in the given patch.
    api.step_data(
      'git diff to analyze patch (2)',
      api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc'),
    ),
    api.resultdb.query(
      inv_bundle=current_patchset_invocations,
      step_name=(
        'collect tasks (with patch).'
        'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'
      ),
    ),
    api.flakiness(check_for_flakiness=True),
    api.resultdb.query_new_test_variants(
      rdb_pb2.QueryNewTestVariantsResponse(
        is_baseline_ready=True,
        new_test_variants=[
          rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
            test_id=(
              'ninja://ios/chrome/test/earl_grey2:'
              'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_a'
            ),
            variant_hash=(
              'b3M6TWFjLTExCnRlc3Rfc3VpdGU6aW9zX2Nocm9tZV9ib29r'
              'bWFya3NfZWcydGVzdHNfbW9kdWxlX2lQYWQgQWlyIDIgMTQuNA=='
            ),
          )
        ],
      ),
      step_name='searching_for_new_tests with ResultDB.query_new_test_variants',
    ),
    api.override_step_data(
      (
        'test new tests for flakiness.'
        'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
        '(check flakiness shard #0) on Mac-11'
      ),
      api.chromium_swarming.canned_summary_output(
        api.json.output({}), failure=False
      ),
    ),
    api.resultdb.query(
      inv_bundle=flaky_results,
      step_name=(
        'test new tests for flakiness.'
        'collect tasks (check flakiness shard #0).'
        'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'
      ),
    ),
    api.post_process(post_process.PropertyEquals, 'do_not_retry', True),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'invalid_test',
    api.chromium_tests_builder_config.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
      builder_db=builder_db,
      try_db=ctbc.TryDatabase.create(
        {
          'fake-try-group': {
            'fake-try-builder': ctbc.TrySpec.create_for_single_mirror(
              builder_group='fake-group',
              buildername='fake-builder',
            ),
          },
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'isolated_scripts': [
            {
              "test": "ios_chrome_bookmarks_eg2tests_module",
              "name": ("ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4"),
              "swarming": {
                "dimensions": {"os": "Mac-11"},
              },
              "test_id_prefix": (
                "ninja://ios/chrome/test/earl_grey2:"
                "ios_chrome_bookmarks_eg2tests_module/"
              ),
            },
          ],
        },
      },
    ),
    api.flakiness(
      check_for_flakiness=True,
    ),
    # This overrides the file check to ensure that we have test files
    # in the given patch.
    api.step_data(
      'git diff to analyze patch (2)',
      api.raw_io.stream_output('chrome/test.cc\ncomponents/file2.cc'),
    ),
    api.resultdb.query(
      inv_bundle=current_patchset_invocations,
      step_name=(
        'collect tasks (with patch).'
        'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 results'
      ),
    ),
    api.flakiness(check_for_flakiness=True),
    api.resultdb.query_new_test_variants(
      rdb_pb2.QueryNewTestVariantsResponse(
        is_baseline_ready=True,
        new_test_variants=[
          rdb_pb2.QueryNewTestVariantsResponse.NewTestVariant(
            test_id=(
              'ninja://ios/chrome/test/earl_grey2:'
              'ios_chrome_bookmarks_eg2tests_module/TestSuite.test_a'
            ),
            variant_hash=(
              'b3M6TWFjLTExCnRlc3Rfc3VpdGU6aW9zX2Nocm9tZV9ib29r'
              'bWFya3NfZWcydGVzdHNfbW9kdWxlX2lQYWQgQWlyIDIgMTQuNA=='
            ),
          )
        ],
      ),
      step_name='searching_for_new_tests with ResultDB.query_new_test_variants',
    ),
    api.override_step_data(
      (
        'test new tests for flakiness.'
        'ios_chrome_bookmarks_eg2tests_module_iPad Air 2 14.4 '
        '(check flakiness shard #0) on Mac-11'
      ),
      api.chromium_swarming.canned_summary_output(
        api.json.output({}), internal_failure=True
      ),
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'cancel-during-checkout',
    api.chromium_tests_builder_config.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
      builder_db=builder_db,
      try_db=ctbc.TryDatabase.create(
        {
          'fake-try-group': {
            'fake-try-builder': ctbc.TrySpec.create_for_single_mirror(
              builder_group='fake-group',
              buildername='fake-builder',
            ),
          },
        }
      ),
    ),
    api.step_data(
      'gclient runhooks (with patch)',
      retcode=-15,
      cancel=True,
      global_shutdown_event='after',
    ),
    api.post_check(post_process.DoesNotRunRE, '.+ \(without patch\)'),
    api.post_process(post_process.DropExpectation),
    api.expect_status('CANCELED'),
  )

  yield api.test(
    'ci_only',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'experimental_test',
              'experiment_percentage': 0,
            },
            {
              'test': 'ci_only_test',
              'ci_only': True,
            },
          ]
        },
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'multiple_types_of_disabled_tests',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'experimental_test',
              'experiment_percentage': 0,
            },
            {
              'test': 'ci_only_test',
              'ci_only': True,
            },
          ]
        },
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'retry_shards_invalid_but_retry_valid_with_failures',
    api.chromium_tests_builder_config.try_build(
      builder_group='tryserver.chromium.test',
      builder='retry-shards',
      builder_db=_TEST_BUILDERS,
      try_db=_TEST_TRYBOTS,
    ),
    api.properties(
      swarm_hashes={'base_unittests': '[dummy hash for base_unittests/size]'}
    ),
    api.chromium_tests.read_targets_spec(
      'chromium.test',
      {
        'retry-shards': {
          'gtest_tests': [
            {
              'test': 'base_unittests',
              'swarming': {},
            }
          ],
        },
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'with patch', invalid=True
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'retry shards with patch', failures=['Test.One']
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', 'without patch', failures=['Test.One']
    ),
    api.post_process(
      post_process.MustRun, 'base_unittests (retry shards with patch)'
    ),
    api.post_process(post_process.MustRun, 'base_unittests (without patch)'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'expand_submodules_target_diffs',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
      experiments=['chromium_checkout.expand_submodules'],
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.filter.analyze_output(
      status='Found dependency',
      test_targets=['removed_test'],
      compile_targets=['removed_compile'],
    ),
    api.override_step_data(
      '[Experimental] analyze submodules.analyze',
      api.json.output(
        {
          'status': 'Found dependency',
          'test_targets': ['added_test'],
          'compile_targets': ['added_compile'],
        }
      ),
    ),
    api.post_process(post_process.MustRun, '[Experimental] analyze submodules'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'expand_submodules_skip_deleted',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
      experiments=['chromium_checkout.expand_submodules'],
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.step_data(
      '[Experimental] git diff --raw to analyze patch',
      api.raw_io.stream_output(':160000 160000 1234567 0000000 D\tsub\n'),
    ),
    api.post_process(
      post_process.StepTextEquals,
      '[Experimental] analyze submodules',
      'skipping analyze:<br/>* deleted submodules detected: sub',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'expand_submodules_skip_nested',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
      experiments=['chromium_checkout.expand_submodules'],
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.path.files_exist(
      api.path.cache_dir / 'builder' / 'src' / 'sub' / '.git'
    ),
    api.step_data(
      '[Experimental] git diff --raw to analyze patch',
      api.raw_io.stream_output(':100644 160000 1234567 89abcdef M\tsub\n'),
    ),
    api.step_data(
      '[Experimental] git diff submodules.sub',
      api.raw_io.stream_output(
        ':160000 160000 1234567 89abcdef M\tnested_sub\n'
      ),
    ),
    api.post_process(
      post_process.StepTextEquals,
      '[Experimental] analyze submodules',
      'skipping analyze:<br/>* nested submodules detected: sub/nested_sub',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'expand_submodules_skip_submodule_deps',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
      experiments=['chromium_checkout.expand_submodules'],
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.path.files_exist(
      api.path.cache_dir / 'builder' / 'src' / 'sub' / '.git'
    ),
    api.step_data(
      '[Experimental] git diff --raw to analyze patch',
      api.raw_io.stream_output(':100644 160000 1234567 89abcdef M\tsub\n'),
    ),
    api.step_data(
      '[Experimental] git diff submodules.sub',
      api.raw_io.stream_output(':100644 100644 1234567 89abcdef M\tDEPS\n'),
    ),
    api.post_process(
      post_process.StepTextEquals,
      '[Experimental] analyze submodules',
      'skipping analyze:<br/>* submodule DEPS modified: sub/DEPS',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'expand_submodules_skip_unresolvable',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
      experiments=['chromium_checkout.expand_submodules'],
    ),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.path.files_exist(
      api.path.cache_dir / 'builder' / 'src' / 'sub' / '.git'
    ),
    api.step_data(
      '[Experimental] git diff --raw to analyze patch',
      api.raw_io.stream_output(':100644 160000 1234567 89abcdef M\tsub\n'),
    ),
    api.step_data('[Experimental] git diff submodules.sub', retcode=1),
    api.post_process(
      post_process.StepTextEquals,
      '[Experimental] analyze submodules',
      'skipping analyze:<br/>* unresolvable submodules detected: sub',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'submodule_analyze_footer_active',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    api.tryserver.get_footers({'Submodule-Gn-Analyze': ['true']}),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.path.files_exist(
      api.path.cache_dir / 'builder' / 'src' / 'sub' / '.git'
    ),
    api.step_data(
      'git diff --raw to analyze patch',
      api.raw_io.stream_output(':100644 160000 1234567 89abcdef M\tsub\n'),
    ),
    api.step_data(
      'git diff submodules.sub',
      api.raw_io.stream_output(
        ':100644 100644 1234567 89abcdef M\tsub_foo.cc\n'
      ),
    ),
    api.post_process(post_process.StepSuccess, 'analyze'),
    api.post_process(
      post_process.StepCommandContains,
      'analyze',
      [
        '{"additional_compile_targets": [], '
        '"files": ["sub", "sub/sub_foo.cc"], '
        '"test_targets": []}',
      ],
    ),
    api.post_process(
      post_process.PropertyEquals,
      'submodule_analyze',
      {
        'test_targets_removed': [],
        'compile_targets_removed': [],
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'submodule_analyze_footer_prunes_targets',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    api.tryserver.get_footers({'Submodule-Gn-Analyze': ['true']}),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            {
              'test': 'retained_test',
            },
            {
              'test': 'removed_test',
            },
          ]
        },
      },
    ),
    api.path.files_exist(
      api.path.cache_dir / 'builder' / 'src' / 'sub' / '.git'
    ),
    api.step_data(
      'git diff --raw to analyze patch',
      api.raw_io.stream_output(':100644 160000 1234567 89abcdef M\tsub\n'),
    ),
    api.step_data(
      'git diff submodules.sub',
      api.raw_io.stream_output(
        ':100644 100644 1234567 89abcdef M\tsub_foo.cc\n'
      ),
    ),
    api.filter.analyze_output(
      status='Found dependency',
      test_targets=['retained_test'],
      compile_targets=['retained_test'],
    ),
    api.post_process(post_process.StepSuccess, 'analyze'),
    api.post_process(
      post_process.LogContains,
      'analyze',
      'test_targets_removed',
      ['removed_test'],
    ),
    api.post_process(
      post_process.LogContains,
      'analyze',
      'compile_targets_removed',
      ['removed_test'],
    ),
    api.post_process(
      post_process.PropertyEquals,
      'submodule_analyze',
      {
        'test_targets_removed': ['removed_test'],
        'compile_targets_removed': ['removed_test'],
      },
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'submodule_analyze_footer_skip_deleted',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    api.tryserver.get_footers({'Submodule-Gn-Analyze': ['true']}),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.step_data(
      'git diff --raw to analyze patch',
      api.raw_io.stream_output(':160000 160000 1234567 0000000 D\tsub\n'),
    ),
    api.post_process(
      post_process.StepTextEquals,
      'analyze',
      'skipping analyze<br/>* deleted submodules detected: sub',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'submodule_analyze_footer_skip_nested',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    api.tryserver.get_footers({'Submodule-Gn-Analyze': ['true']}),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.path.files_exist(
      api.path.cache_dir / 'builder' / 'src' / 'sub' / '.git'
    ),
    api.step_data(
      'git diff --raw to analyze patch',
      api.raw_io.stream_output(':100644 160000 1234567 89abcdef M\tsub\n'),
    ),
    api.step_data(
      'git diff submodules.sub',
      api.raw_io.stream_output(
        ':160000 160000 1234567 89abcdef M\tnested_sub\n'
      ),
    ),
    api.post_process(
      post_process.StepTextEquals,
      'analyze',
      'skipping analyze<br/>* nested submodules detected: sub/nested_sub',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'submodule_analyze_footer_skip_submodule_deps',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    api.tryserver.get_footers({'Submodule-Gn-Analyze': ['true']}),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.path.files_exist(
      api.path.cache_dir / 'builder' / 'src' / 'sub' / '.git'
    ),
    api.step_data(
      'git diff --raw to analyze patch',
      api.raw_io.stream_output(':100644 160000 1234567 89abcdef M\tsub\n'),
    ),
    api.step_data(
      'git diff submodules.sub',
      api.raw_io.stream_output(':100644 100644 1234567 89abcdef M\tDEPS\n'),
    ),
    api.post_process(
      post_process.StepTextEquals,
      'analyze',
      'skipping analyze<br/>* submodule DEPS modified: sub/DEPS',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'submodule_analyze_footer_skip_unresolvable',
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    api.tryserver.get_footers({'Submodule-Gn-Analyze': ['true']}),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.path.files_exist(
      api.path.cache_dir / 'builder' / 'src' / 'sub' / '.git'
    ),
    api.step_data(
      'git diff --raw to analyze patch',
      api.raw_io.stream_output(':100644 160000 1234567 89abcdef M\tsub\n'),
    ),
    api.step_data('git diff submodules.sub', retcode=1),
    api.post_process(
      post_process.StepTextEquals,
      'analyze',
      'skipping analyze<br/>* unresolvable submodules detected: sub',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'submodule_analyze_footer_skips_code_coverage',
    api.platform('linux', 64),
    api.chromium.try_build(
      builder_group='fake-try-group',
      builder='fake-try-builder',
    ),
    api.tryserver.get_footers({'Submodule-Gn-Analyze': ['true']}),
    ctbc_api.properties(
      ctbc_api.properties_assembler_for_try_builder()
      .with_mirrored_builder(
        builder_group='fake-group',
        builder='fake-builder',
      )
      .assemble()
    ),
    api.code_coverage(use_clang_coverage=True),
    api.path.files_exist(
      api.path.cache_dir / 'builder' / 'src' / 'sub' / '.git'
    ),
    api.step_data(
      'git diff --raw to analyze patch',
      api.raw_io.stream_output(':100644 160000 1234567 89abcdef M\tsub\n'),
    ),
    api.step_data(
      'git diff submodules.sub',
      api.raw_io.stream_output(
        ':100644 100644 1234567 89abcdef M\tsub_foo.cc\n'
      ),
    ),
    api.post_process(
      post_process.StepSuccess,
      'Skip instrumentating code coverage because DEPS only change',
    ),
    api.post_process(post_process.DropExpectation),
  )
