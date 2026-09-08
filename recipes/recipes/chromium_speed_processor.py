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
    file,
    json,
    path,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  file: file.API
  json: json.API
  path: path.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  file: file.TEST_API
  path: path.TEST_API
  properties: properties.TEST_API

from recipe_engine import post_process
from PB.recipes.build.chromium_speed_processor import InputProperties
from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

PROPERTIES = InputProperties


def read_processor_spec(api: DEPS, file_path):
  """Reads the contents of a json file from given file_path."""
  content = api.file.read_json(
      'read processor spec file (%s)' % api.path.basename(file_path),
      file_path,
      test_data={})
  return content


def RunSteps(api: DEPS, properties):
  with api.chromium.chromium_layout():
    # 1. update the bot to have latest scripts
    builder_id, builder_config = api.chromium_tests_builder_config.lookup_builder(
    )
    execution_mode = builder_config.execution_mode
    if execution_mode != ctbc.TEST:
      api.step.empty(
          'chromium_speed_tester',
          status=api.step.INFRA_FAILURE,
          step_text='Unexpected execution mode. Expect: %s, Actual: %s' %
          (ctbc.TEST, execution_mode))
    api.chromium_tests.configure_build(builder_config)
    update_result, _, _ = api.chromium_tests.prepare_checkout(
        builder_config, timeout=3600, no_fetch_tags=True)

    # 2. run collect task for each group
    task_groups = api.json.loads(properties.tasks_groups)
    tester_properties = api.json.loads(properties.tester_properties)

    source_dir = update_result.source_root.path
    # TODO(crbug.com/399205632): Hide the processor spec file into build config
    processor_spec = read_processor_spec(
        api,
        file_path=source_dir.joinpath('tools', 'perf',
                                      'chromium.perf.processors.json'))
    merge_setting = processor_spec.get(builder_id.builder, {}).get('merge', {})
    merge_script = source_dir.joinpath(merge_setting.get('script', ''))
    merge_arguments = merge_setting.get('args', [])

    for group_name, task_ids in task_groups.items():
      collect_task_args = api.chromium_swarming.get_collect_task_args(
          merge_script=merge_script,
          merge_arguments=merge_arguments,
          build_properties=tester_properties,
          requests_json=task_ids)

      step_result = api.chromium_swarming.run_collect_task_script(
          group_name, collect_task_args)

      step_result.presentation.step_text = 'merging...'
      step_result.presentation.logs['Merge script log'] = [
          f'merge-script: {str(merge_script)}',
          f'merge-script-arguments: {str(merge_arguments)}',
          step_result.raw_io.output
      ]


MOCK_TASK_GROUPS = """
                    {
                      "performance_test_suite":
                      {
                        "tasks": [ { "task_id": "4b9894c1f295c310" }]
                      }
                    }
                    """
MOCK_PROR_JSON_STRING = """
                        {
                          "builder_name":
                            "linux-perf",
                          "build_number":
                            "666",
                          "perf_dashboard_machine_group":
                            "ChromiumPerfFyi",
                          "got_revision_cp":
                            "refs/heads/main@{#758236}",
                          "got_v8_revision":
                            "0bb2b3008e7530eb4ac3f4c68d328649ff662e30",
                          "got_webrtc_revision":
                            "9f0b36c4610de8e0fe4bde2f57c8bc487e3a1005"
                        }
                        """


def GenTests(api: TEST_DEPS):
  builder_db = ctbc.BuilderDatabase.create({
      'fake_group': {
          'fake_builder':
              ctbc.BuilderSpec.create(
                  chromium_config='chromium',
                  gclient_config='chromium',
              ),
          'fake_triggered_tester':
              ctbc.BuilderSpec.create(
                  chromium_config='chromium',
                  gclient_config='chromium',
                  parent_buildername='fake_builder',
                  execution_mode=ctbc.TEST,
              ),
          'fake_triggered_processor':
              ctbc.BuilderSpec.create(
                  chromium_config='chromium',
                  gclient_config='chromium',
                  parent_buildername='fake_triggered_tester',
                  execution_mode=ctbc.TEST,
              ),
      }
  })
  processor_spec = {
      'fake_triggered_processor': {
          'merge': {
              'args': ['--foo', '--bar'],
              'script': '//tools/perf/merge_script.py'
          }
      }
  }

  yield api.test(
      'recipe-coverage',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake_group',
          builder='fake_triggered_processor',
          builder_db=builder_db),
      api.properties(
          InputProperties(
              tasks_groups=MOCK_TASK_GROUPS,
              tester_properties=MOCK_PROR_JSON_STRING)),
      api.post_process(post_process.DoesNotRun, 'trigger'),
      api.post_process(post_process.MustRun, 'performance_test_suite'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'builder-coverage',
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake_group',
          builder='fake_builder',
          builder_db=builder_db),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retrieve_merge_script_from_processor_spec',
      api.properties(
          InputProperties(
              tasks_groups=MOCK_TASK_GROUPS,
              tester_properties=MOCK_PROR_JSON_STRING),
          swarm_hashes={'fake_test': 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeee/size'},
      ),
      api.chromium_tests_builder_config.ci_build(
          builder_group='fake_group',
          builder='fake_triggered_processor',
          builder_db=builder_db),
      api.step_data('read processor spec file (chromium.perf.processors.json)',
                    api.file.read_json(processor_spec)),
      api.post_process(post_process.DoesNotRun, 'trigger'),
      api.post_process(
          post_process.LogContains, 'performance_test_suite',
          'Merge script log', [
              'merge-script', '[CACHE]/builder/src/tools/perf/merge_script.py',
              'merge-script-arguments', '--foo', '--bar'
          ]),
      api.post_process(post_process.DropExpectation),
  )
