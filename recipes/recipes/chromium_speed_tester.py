# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  json,
  platform,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  context: context.API
  json: json.API
  platform: platform.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API


NO_SUFFIX = ''

from recipe_engine import post_process
from PB.recipes.build.chromium_speed_tester import InputProperties
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

PROPERTIES = InputProperties


def RunSteps(api: DEPS, properties):
  with api.chromium.chromium_layout():
    builder_id, builder_config = (
      api.chromium_tests_builder_config.lookup_builder()
    )
    api.chromium_tests.report_builders(builder_config)
    execution_mode = builder_config.execution_mode
    if execution_mode != ctbc.TEST:
      api.step.empty(
        'chromium_speed_tester',
        status=api.step.INFRA_FAILURE,
        step_text=(
          'Unexpected execution mode. Expect: %s, Actual: %s'
          % (ctbc.TEST, execution_mode)
        ),
      )
    api.chromium_tests.configure_build(builder_config)
    update_result, build_dir, build_config = (
      api.chromium_tests.prepare_checkout(
        builder_config, timeout=3600, no_fetch_tags=True
      )
    )
    checkout_dir = update_result.checkout_dir
    source_dir = update_result.source_root.path
    api.chromium_tests.lookup_builder_gn_args(
      source_dir, builder_id, builder_config
    )
    tests = build_config.tests_on(builder_id)
    api.chromium_tests.download_command_lines_for_tests(
      source_dir, build_dir, tests, builder_config
    )

    env = {}
    # Mac perf testers have a different behaviour when this environment var is
    # set i.e. Chrome is started using 'open' command. See crbug/1454294
    if api.platform.is_mac:
      env['START_BROWSER_WITH_DEFAULT_PRIORITY'] = '1'
    with api.context(env=env):
      test_failure_summary = api.chromium_tests.run_tests(
        checkout_dir,
        source_dir,
        build_dir,
        builder_id,
        builder_config,
        tests,
      )

    task_groups = {
      t.get_task(NO_SUFFIX).request.name: t.get_task(
        NO_SUFFIX
      ).collect_cmd_input()
      for t in tests
    }
    tester_properties = {
      'buildername': api.buildbucket.builder_name,
      'buildnumber': api.buildbucket.build.number,
      'perf_dashboard_machine_group': properties.perf_dashboard_machine_group,
      'got_revision_cp': properties.parent_got_revision_cp,
      'got_v8_revision': properties.parent_got_v8_revision,
      'got_webrtc_revision': properties.parent_got_webrtc_revision,
    }
    additional_trigger_properties = {
      'tasks_groups': api.json.dumps(task_groups),
      'tester_properties': api.json.dumps(tester_properties),
    }

    api.chromium_tests.trigger_child_builds(
      builder_id,
      update_result,
      builder_config,
      additional_properties=additional_trigger_properties,
    )
    return test_failure_summary


def GenTests(api: TEST_DEPS):
  yield api.test(
    'tester-coverage',
    api.chromium_tests_builder_config.ci_build(
      builder_group='chromium.perf',
      builder='linux-r350-perf',
      parent_buildername='linux-builder-perf',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'builder-coverage',
    api.chromium_tests_builder_config.ci_build(
      builder_group='chromium.perf', builder='linux-builder-perf'
    ),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'mac-tester-coverage',
    api.chromium_tests_builder_config.ci_build(
      builder_group='chromium.perf',
      builder='mac-intel-perf',
      parent_buildername='mac-builder-perf',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'tester_does_not_trigger_processor',
    api.properties(
      swarm_hashes={'fake_test': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size'},
    ),
    api.platform('linux', 64),
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake_group',
      builder='fake_triggered_tester',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake_group': {
            'fake_builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            'fake_triggered_tester': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              parent_buildername='fake_builder',
              execution_mode=ctbc.TEST,
            ),
          }
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      'fake_group',
      {
        'fake_triggered_tester': {
          'isolated_scripts': [
            {
              'name': 'fake_test',
              'merge': {
                'args': ['--foo', '--bar'],
                'script': '//path/to/script.py',
              },
              'swarming': {
                'dimensions': {
                  'os': 'Ubuntu-22.04',
                },
              },
            }
          ],
        }
      },
    ),
    api.post_process(post_process.MustRun, 'fake_test on Ubuntu-22.04'),
    api.post_process(post_process.DoesNotRun, 'trigger'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'tester_pass_trigger_properties_to_processor',
    api.properties(
      swarm_hashes={'fake_test': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size'},
    ),
    api.platform('linux', 64),
    api.chromium_tests_builder_config.ci_build(
      builder_group='fake_group',
      builder='fake_triggered_tester',
      builder_db=ctbc.BuilderDatabase.create(
        {
          'fake_group': {
            'fake_builder': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
            ),
            'fake_triggered_tester': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              parent_buildername='fake_builder',
              execution_mode=ctbc.TEST,
            ),
            'fake_triggered_processor': ctbc.BuilderSpec.create(
              chromium_config='chromium',
              gclient_config='chromium',
              parent_buildername='fake_triggered_tester',
              execution_mode=ctbc.TEST,
            ),
          }
        }
      ),
    ),
    api.chromium_tests.read_targets_spec(
      'fake_group',
      {
        'fake_triggered_tester': {
          'isolated_scripts': [
            {
              'name': 'fake_test',
              'merge': {
                'args': ['--foo', '--bar'],
                'script': '//path/to/script.py',
              },
              'swarming': {
                'dimensions': {
                  'os': 'Ubuntu-22.04',
                },
              },
            }
          ],
        }
      },
    ),
    api.post_process(post_process.MustRun, 'fake_test on Ubuntu-22.04'),
    api.post_process(post_process.MustRun, 'trigger'),
    api.post_process(
      post_process.LogContains,
      'trigger',
      'input',
      [
        'buildername',
        'buildnumber',
        'perf_dashboard_machine_group',
        'got_revision_cp',
        'got_v8_revision',
        'got_webrtc_revision',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )
