# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_swarming,
  chromium_tests,
  isolate,
  pgo,
  profiles,
  test_utils,
)
from RECIPE_MODULES.depot_tools import bot_update
from RECIPE_MODULES.recipe_engine import (
  assertions,
  buildbucket,
  commit_position,
  json,
  path,
  platform,
  properties,
  step,
  swarming,
)


@dataclass
class DEPS(RecipeScriptApi):
  assertions: assertions.API
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  commit_position: commit_position.API
  isolate: isolate.API
  json: json.API
  path: path.API
  pgo: pgo.API
  platform: platform.API
  profiles: profiles.API
  properties: properties.API
  step: step.API
  swarming: swarming.API
  test_utils: test_utils.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  chromium_swarming: chromium_swarming.TEST_API
  chromium_tests: chromium_tests.TEST_API
  pgo: pgo.TEST_API
  properties: properties.TEST_API
  swarming: swarming.TEST_API


from recipe_engine import post_process

from RECIPE_MODULES.build.chromium_tests import steps


def RunSteps(api: DEPS):
  api.chromium.set_config(
    'chromium', TARGET_PLATFORM=api.properties.get('target_platform', 'linux')
  )

  # Fake path, as the real one depends on having done a chromium checkout.
  checkout_dir = api.path.start_dir
  source_dir = checkout_dir / 'fake-repo'
  build_dir = source_dir / 'out' / 'some_build_dir'
  api.profiles.source_dir = source_dir
  api.chromium_swarming.path_to_merge_scripts = source_dir / 'merge_scripts'
  api.chromium_swarming.set_default_dimension('pool', 'foo')
  api.chromium.set_build_properties(
    {
      'got_webrtc_revision': 'webrtc_sha',
      'got_v8_revision': 'v8_sha',
      'got_revision': 'd3adv3ggie',
      'got_revision_cp': 'refs/heads/main@{#54321}',
    }
  )

  test_spec = steps.SwarmingGTestTestSpec.create(
    'base_unittests',
    server=api.properties.get('server'),
    isolate_profile_data=api.properties.get('isolate_profile_data', False),
    dimensions=api.properties.get('dimensions', {'os': 'Linux'}),
  )
  test = test_spec.get_test(api.chromium_tests)

  test_options = steps.TestOptions.create()
  test.test_options = test_options

  try:
    assert len(test.get_invocation_names('')) == 0
    invalid_tests, failed_tests = api.test_utils.run_tests_once(
      checkout_dir, source_dir, build_dir, [test], ''
    )
    assert len(test.get_invocation_names('')) > 0
    assert test.runs_on_swarming
  finally:
    api.step('details', [])
    api.step.active_result.presentation.logs['details'] = [
      'compile_targets: %r' % test.compile_targets(),
      'uses_local_devices: %r' % test.uses_local_devices,
      'uses_isolate: %r' % test.uses_isolate,
    ]
  if invalid_tests or failed_tests:
    raise api.step.StepFailure(
      'failed: %s' % ' '.join(t.name for t in failed_tests + invalid_tests)
    )


def GenTests(api: TEST_DEPS):

  yield api.test(
    'basic',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/111',
      },
      orchestrator={'builder_name': 'orchestrator'},
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'android',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      target_platform='android',
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
    ),
    api.post_process(
      api.swarming.check_triggered_request,
      'test_pre_run.[trigger] base_unittests',
      lambda check, req: check('target_platform:android' in req[0].command),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_result_json',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      }
    ),
    api.override_step_data(
      'base_unittests',
      api.chromium_swarming.canned_summary_output(
        dispatched_task_step_test_data=None, failure=True, retcode=1
      ),
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.StepFailure, 'base_unittests'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'isolate_profile_data',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.pgo(use_pgo=False),
    api.properties(
      isolate_profile_data=True,
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
    ),
    api.post_check(
      api.swarming.check_triggered_request,
      'test_pre_run.[trigger] base_unittests',
      lambda check, req: check(
        req[0].env_vars['LLVM_PROFILE_FILE']
        == '${ISOLATED_OUTDIR}/profraw/default-%2m%c.profraw'
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'isolate_profile_data_windows',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.pgo(use_pgo=False),
    api.properties(
      isolate_profile_data=True,
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
      dimensions={
        'os': 'Windows',
      },
    ),
    api.post_check(
      api.swarming.check_triggered_request,
      'test_pre_run.[trigger] base_unittests on Windows',
      lambda check, req: check(
        req[0].env_vars['LLVM_PROFILE_FILE']
        == '${ISOLATED_OUTDIR}/profraw/default-%2m.profraw'
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'using_pgo',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.pgo(use_pgo=True),
    api.properties(
      isolate_profile_data=True,
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/size',
      },
    ),
    api.post_check(
      api.swarming.check_triggered_request,
      'test_pre_run.[trigger] base_unittests',
      lambda check, req: check(
        req[0].env_vars['LLVM_PROFILE_FILE']
        == '${ISOLATED_OUTDIR}/profraw/default-%2m.profraw'
      ),
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'other_server',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/111',
      },
      server='other-swarming.appspot.com',
    ),
    api.post_process(
      post_process.StepCommandContains,
      'test_pre_run.[trigger] base_unittests',
      ['-server', 'other-swarming.appspot.com'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'rdb_results_html_escape',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      swarm_hashes={
        'base_unittests': 'ffffffffffffffffffffffffffffffffffffffff/111',
      },
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'base_unittests', '', failures=['test 1 <img src=x onerror=alert(1)>']
    ),
    api.expect_status('FAILURE'),
    api.post_process(
      post_process.StepTextContains,
      'base_unittests',
      ['[test 1 &lt;img src=x onerror=alert(1)&gt;]'],
    ),
    api.post_process(post_process.DropExpectation),
  )
