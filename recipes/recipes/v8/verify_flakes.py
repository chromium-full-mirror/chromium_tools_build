# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Recipe to verify flaky tests (aka progression testing).

The recipe reads flake config and for each entry triggers flako recipe in
reproduce_only mode. If any flake fails to reproduce, the build is marked as
failed, which can then be used to alert sheriffs via a LUCI-Notify rule.
"""

import ast
import re

from PB.go.chromium.org.luci.buildbucket.proto import (
  builds_service as builds_service_pb2,
)
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2

from recipe_engine.post_process import (
  DropExpectation,
  Filter,
  MustRun,
  StepException,
  StepFailure,
  SummaryMarkdownRE,
)


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8, v8_tests
from RECIPE_MODULES.depot_tools import gitiles
from RECIPE_MODULES.recipe_engine import buildbucket, json, step


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  gitiles: gitiles.API
  json: json.API
  step: step.API
  v8: v8.API
  v8_tests: v8_tests.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  gitiles: gitiles.TEST_API
  json: json.TEST_API
  step: step.TEST_API


MAX_CONFIGS = 16
URL_FORMAT_RE = re.compile(r'(?:https?://)?(.+)')

TEST_CONFIG = [
  {
    'bisect_buildername': 'V8 Linux64 - debug builder',
    'bisect_builder_group': 'client.v8',
    'bug_url': 'https://crbug.com/v8/8744',
    'build_config': 'Debug',
    'extra_args': [],
    'isolated_name': 'bot_default',
    'num_shards': 2,
    'repetitions': 5000,
    'swarming_dimensions': [
      'cpu:x86-64-avx2',
      'gpu:none',
      'os:Ubuntu-16.04',
      'pool:Chrome',
    ],
    'test_name': 'cctest/test-cpu-profiler/FunctionCallSample',
    'timeout_sec': 60,
    'total_timeout_sec': 120,
    'variant': 'interpreted_regexp',
  }
]


def update_step_presentation(api: DEPS, presentation, build, flake_config):
  presentation.links['build %s' % build.id] = api.buildbucket.build_url(
    build_id=build.id
  )
  bug_url = flake_config.get('bug_url')
  if bug_url:
    presentation.links[URL_FORMAT_RE.match(bug_url).group(1)] = bug_url
  presentation.logs['flake config'] = api.json.dumps(
    flake_config, indent=2
  ).splitlines()
  if build.status == common_pb2.FAILURE:
    presentation.step_text = (
      'failed to reproduce<br/>please consider re-enabling this test'
    )
    presentation.status = api.step.FAILURE
  elif build.status == common_pb2.SUCCESS:
    presentation.step_text = 'reproduced'
  else:
    presentation.step_text = 'failed to execute'
    presentation.status = api.step.EXCEPTION


def RunSteps(api: DEPS):
  configs = ast.literal_eval(
    api.gitiles.download_file(
      'https://chromium.googlesource.com/v8/v8',
      'flakes/flakes.pyl',
      branch='infra/config',
      step_name='read flake config',
    )
  )
  if not configs:
    api.step('No flakes to reproduce', cmd=None)
    return

  if len(configs) > MAX_CONFIGS:
    too_many_flakes = api.step('Too many flake configs', cmd=None)
    too_many_flakes.presentation.status = api.step.FAILURE
    too_many_flakes.presentation.step_text = (
      'Running on first %d configs only' % MAX_CONFIGS
    )
    configs = configs[:MAX_CONFIGS]

  v8_commits, _ = api.gitiles.log(
    'https://chromium.googlesource.com/v8/v8/',
    'refs/heads/main',
    limit=1,
    step_name='read V8 ToT revision',
  )
  v8_tot = v8_commits[0]['commit']
  # TODO(sergiyb): Stop setting got_revision property when build manifests
  # are supported in Milo, in which case using set_output_gitiles_commit should
  # be sufficient.
  api.step.active_result.presentation.properties['got_revision'] = v8_tot
  api.buildbucket.set_output_gitiles_commit(
    common_pb2.GitilesCommit(
      host='chromium.googlesource.com',
      project='v8/v8',
      ref='refs/heads/main',
      id=v8_tot,
    )
  )
  builds = api.v8.trigger.buildbucket(
    [
      (
        'v8_flako',
        dict(
          flako_properties,
          mode='repro',
          swarming_priority=40,
          revision=v8_tot,
          max_calibration_attempts=1,
          swarming_expiration=7200,  # 2 hours
        ),
      )
      for flako_properties in configs
    ],
    step_name='trigger flako builds',
  )

  # Collect builds.
  build_results = []
  with api.step.nest('collect builds'):
    for index, build in enumerate(builds):
      label = api.v8_tests.ui_test_label(configs[index]['test_name'])
      build_results.append(
        api.buildbucket.collect_build(
          int(build.id), step_name=label, mirror_status=True, timeout=4 * 3600
        )
      )

  # Emit summary steps for each build.
  non_flaky_tests = []
  for index, build in enumerate(build_results):
    label = api.v8_tests.ui_test_label(configs[index]['test_name'])
    step_result = api.step(label, cmd=None)
    update_step_presentation(
      api, step_result.presentation, build, configs[index]
    )

    if build.status == common_pb2.FAILURE:
      non_flaky_tests.append(label)

  if non_flaky_tests:
    raise api.step.StepFailure(
      'Some flakes failed to reproduce: %s' % ', '.join(non_flaky_tests)
    )

  api.step('No flakes that fail to reproduce', cmd=None)


def GenTests(api: TEST_DEPS):

  def test_data(results, ui_test_name=None):
    return sum(
      [
        api.step_data(
          'read flake config',
          api.gitiles.make_encoded_file(api.json.dumps(TEST_CONFIG)),
        ),
        api.step_data(
          'read V8 ToT revision', api.gitiles.make_log_test_data('deadbeef')
        ),
        api.buildbucket.generic_build(
          project='v8', bucket='try.triggered', builder='v8_verify_flakes'
        ),
        api.buildbucket.simulated_schedule_output(
          builds_service_pb2.BatchResponse(
            responses=[dict(schedule_build=dict(id=123))],
          ),
          step_name='trigger flako builds',
        ),
        api.buildbucket.simulated_collect_output(
          [
            api.buildbucket.ci_build_message(build_id=123, status=result)
            for result in results
          ],
          step_name='collect builds.' + (ui_test_name or 'FunctionCallSample'),
        ),
      ],
      api.empty_test_data(),
    )

  yield api.test(
    'success',
    test_data(['SUCCESS']),
    status='SUCCESS',
  )

  yield api.test(
    'failure',
    test_data(['FAILURE']),
    api.post_process(StepFailure, 'FunctionCallSample'),
    api.post_process(
      SummaryMarkdownRE, 'Some flakes failed to reproduce: FunctionCallSample'
    ),
    api.post_process(DropExpectation),
    status='FAILURE',
  )

  yield api.test(
    'infra_failure',
    test_data(['INFRA_FAILURE']),
    api.post_process(StepException, 'FunctionCallSample'),
    api.post_process(Filter().include_re(r'.*FunctionCallSample.*')),
    status='SUCCESS',
  )

  yield api.test(
    'no_flakes',
    api.step_data('read flake config', api.gitiles.make_encoded_file('[]')),
    api.post_process(MustRun, 'No flakes to reproduce'),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )

  yield api.test(
    'too_many_flakes',
    test_data(['SUCCESS'] * 20, ui_test_name='baz'),
    api.override_step_data(
      'read flake config',
      api.gitiles.make_encoded_file(
        api.json.dumps([{'test_name': 'foo/bar/baz'}] * 20)
      ),
    ),
    api.post_process(MustRun, 'Too many flake configs'),
    api.post_process(DropExpectation),
    status='SUCCESS',
  )
