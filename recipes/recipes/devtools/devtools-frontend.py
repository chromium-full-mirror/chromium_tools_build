# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import json

from google.protobuf import timestamp_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
    common as common_pb2,  # go/pyformat-break
    invocation as invocation_pb2,  #
    test_result as test_result_pb2,  #
)
from recipe_engine.post_process import (DoesNotRun, DropExpectation, Filter,
                                        MustRun, SummaryMarkdown)
from PB.recipes.build.devtools.devtools_frontend import InputProperties

from RECIPE_MODULES.build.devtools.api_tests_runner import ApiTests
from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.test_runner_base import FLAKE_DETECTION_MAX_TESTS
from RECIPE_MODULES.build.devtools.e2e_tests_runner import E2ETests
from RECIPE_MODULES.build.devtools.performance_tests_runner import PerformanceTests
from RECIPE_MODULES.build.devtools.test_phases import run_test_pipelines
from RECIPE_MODULES.build.devtools.unit_tests_runner import UnitTests


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    builder_group,
    chromium,
    chromium_swarming,
    devtools,
    perf_dashboard,
    v8,
)
from RECIPE_MODULES.depot_tools import (
    bot_update,
    depot_tools,
    git,
    gsutil,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    cas,
    context,
    file,
    futures,
    path,
    platform,
    properties,
    random,
    raw_io,
    resultdb,
    step,
    swarming,
    time,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  cas: cas.API
  chromium: chromium.API
  chromium_swarming: chromium_swarming.API
  context: context.API
  depot_tools: depot_tools.API
  devtools: devtools.API
  file: file.API
  futures: futures.API
  git: git.API
  gsutil: gsutil.API
  path: path.API
  perf_dashboard: perf_dashboard.API
  platform: platform.API
  properties: properties.API
  random: random.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  swarming: swarming.API
  time: time.API
  tryserver: tryserver.API
  v8: v8.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  buildbucket: buildbucket.TEST_API
  builder_group: builder_group.TEST_API
  chromium_swarming: chromium_swarming.TEST_API
  context: context.TEST_API
  file: file.TEST_API
  git: git.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  resultdb: resultdb.TEST_API
  step: step.TEST_API
  time: time.TEST_API
  tryserver: tryserver.TEST_API

PROPERTIES = InputProperties


def RunSteps(api: DEPS, properties):
  builder_config = properties.builder_config or 'Release'
  coverage = properties.coverage
  if 'coverage' not in api.properties:
    coverage = True

  api.devtools.configure(builder_config, properties.is_official_build)
  api.devtools.update()

  build_dir = api.devtools.source_dir / 'out' / api.chromium.c.build_config_fs
  with api.devtools.depot_on_path():
    api.devtools.clean_out_dir(builder_config, True)
    with api.chromium.guard_compile(build_dir):
      api.chromium.run_gn(api.devtools.source_dir, build_dir)

      compilation_result = api.chromium.compile(api.devtools.source_dir,
                                                build_dir)
      if compilation_result.status != common_pb.SUCCESS:
        return compilation_result
    cas_digest = api.devtools.archive_to_cas()

    trigger = SwarmingTrigger(api, cas_digest)
    tests = [
        UnitTests(api, trigger, builder_config, coverage, 'Unit Tests'),
        UnitTests(
            api,
            trigger,
            builder_config,
            coverage,
            'Unit Tests (node)',
            node_unit_tests=True),
        ApiTests(api, trigger, builder_config, 'API Tests'),
        E2ETests(api, trigger, builder_config, 'E2E Tests'),
        PerformanceTests(api, trigger, builder_config, 'Performance Tests'),
    ]
    tests = [t for t in tests if not t.skip()]

    lint_future = api.futures.spawn(run_lint_check, api, builder_config)
    results = run_test_pipelines(api, tests)
    lint_future.result()

    publish_coverage_points(api, skip=not coverage)
    publish_performance_benchmarks(api, skip=not properties.perf_benchmarks)

    return results.raw_result()


def run_lint_check(api: DEPS, builder_config):
  is_debug_build = api.devtools.is_debug(builder_config)
  if is_debug_build or not api.platform.is_linux:
    return
  with api.step.nest('Linting'), api.context(cwd=api.devtools.source_dir):
    api.devtools.run_node_script('Run lint check', 'run_lint_check.mjs')



def publish_performance_benchmarks(api: DEPS, skip):
  if skip:
    return
  report_file = api.devtools.source_dir / 'perf-data/devtools-perf.json'
  front_end_results = api.file.read_json('Read performance data results',
                                         report_file)
  tmp_dir = api.path.mkdtemp('perf-results')
  results_file = tmp_dir / 'devtools-perf.json'
  git_revision = api.bot_update.last_returned_properties['got_revision']
  api.file.write_json(
      'Write Skia Perf format', results_file, {
          'version': 1,
          'git_hash': git_revision,
          'key': {
              'master': 'client.devtools-frontend.integration',
              'bot': api.buildbucket.builder_name
          },
          'results': front_end_results
      })
  save_perf_data_in_bucket(api, results_file)


def save_perf_data_in_bucket(api: DEPS, report_file):
  bucket = 'devtools-frontend-perf'
  today = api.time.utcnow().strftime('%Y/%m/%d/%H')
  upload_name = '{}/{}/{}/{}/{}/{}'.format(
      'ingest', today, 'client.devtools-frontend.integration',
      api.buildbucket.builder_name, 'performance-tests', 'perf-data.json')
  api.step.empty(f'Upload name {upload_name}')
  api.gsutil.upload(
      source=report_file,
      bucket=bucket,
      dest=upload_name,
      link_name=upload_name,
      name='Upload perf data',
  )


def test_cov_data():
  return {
      "total": {
          "lines": {
              "pct": 11.11
          },
          "statements": {
              "pct": 11.12
          },
          "functions": {
              "pct": 11.13
          },
          "branches": {
              "pct": 11.14
          },
      }
  }


def publish_coverage_points(api: DEPS, skip):
  if api.tryserver.is_tryserver or skip:
    return
  with api.step.nest('Coverage'):
    try:
      dimensions = ["lines", "statements", "functions", "branches"]

      report_file = (
          api.devtools.source_dir / 'karma-coverage/coverage-summary.json')

      summary = api.file.read_json('Coverage summary', report_file)
      totals = summary['total']
      api.step.active_result.presentation.step_text = "".join([
          "\n%s: %s%%" % (dim.capitalize(), totals[dim]['pct'])
          for dim in dimensions
      ])

      with api.context(cwd=api.devtools.source_dir):
        git_revision = api.bot_update.last_returned_properties['got_revision']

        commit_count = api.git(
            'rev-list',
            '--count',
            git_revision,
            name='Retrieve commit count',
            stdout=api.raw_io.output_text(),
            step_test_data=lambda: api.raw_io.test_api.stream_output_text(
                '123\n')).stdout.strip()

      points = [
          _point(api, dim, summary['total'], commit_count) for dim in dimensions
      ]
      #TODO(liviurau) find another way arroud 400 error "Invalid ID (revision)
      # 1055; compared to previous ID 0, it was larger or smaller by too much."
      api.perf_dashboard.add_point(points, halt_on_failure=False)
    except Exception:
      api.step.empty('Coverage data not available')


def _point(api: DEPS, dimension, totals, commit_count):
  p = {
      'master': api.builder_group.for_current,
      'bot': api.buildbucket.builder_name,
      'test': '/'.join(['devtools.infra', 'coverage_v2', dimension]),
      'revision': int(commit_count),
      'value': totals[dimension]['pct'],
      'masterid': api.builder_group.for_current,
      'buildername': api.buildbucket.builder_name,
      'buildnumber': api.buildbucket.build.number,
  }

  p['supplemental_columns'] = {
      'a_default_rev': 'r_devtools_git',
      'r_devtools_git': api.bot_update.last_returned_properties['got_revision'],
  }
  return p


def GenTests(api: TEST_DEPS):
  git_repo = 'https://chromium.googlesource.com/devtools/devtools-frontend'

  def ci_build(builder):
    return api.buildbucket.ci_build(
        project='devtools', builder=builder, git_repo=git_repo)

  def try_build(builder, **kwargs):
    return api.buildbucket.try_build(
        project='devtools',
        builder=builder,
        git_repo=git_repo,
        change_number=91827,
        patch_set=1,
        **kwargs)

  yield api.test(
      'compile failure',
      api.builder_group.for_current('devtools-frontend'),
      ci_build(builder='linux'),
      api.step_data('compile', retcode=1),
      status='FAILURE',
  )

  yield api.test(
      'debug cov',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(builder_config='Debug'),
      api.override_step_data('Coverage.Coverage summary',
                             api.file.read_json(test_cov_data())),
  )

  yield api.test(
      'official build',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(is_official_build=True, builder_config='Debug'),
      api.post_process(Filter('gn')),
      status='SUCCESS',
  )


  yield api.test(
      'skip coverage',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(coverage=False, builder_config='Debug'),
      api.post_process(Filter('gn')),
      status='SUCCESS',
  )

  yield api.test(
      'perf data',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(perf_benchmarks=True, builder_config='Debug'),
      api.path.exists(
          api.path.cache_dir /
          'builder/devtools-frontend/perf-data/devtools-perf.json',),
  )

  yield api.test(
      'run performance benchmarks',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(perf_benchmarks=True, builder_config='Debug'),
      api.post_process(Filter('gn')),
      status='SUCCESS',
  )

  yield api.test(
      'cq parallel builder',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='devtools_frontend_linux_rel'),
      api.post_process(MustRun, 'archive'),
      api.post_process(MustRun,
                       'Pipeline E2E Tests.Run tests.Trigger E2E Tests'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'ci parallel builder performance benchmarks',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.properties(perf_benchmarks=True),
      api.post_process(MustRun, 'archive'),
      api.post_process(MustRun,
                       'Pipeline Unit Tests.Run tests.Trigger Unit Tests'),
      api.post_process(MustRun,
                       'Pipeline E2E Tests.Run tests.Trigger E2E Tests'),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(
          MustRun, 'Pipeline Performance Tests.Run tests.Performance Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      api.post_process(Filter().include_re(
          '.*Pipeline.*\.Run tests\.Trigger.*|.*\(Shard #\d*\).*')),
      status='SUCCESS',
  )

  data1 = {
      'shards': [{
          'state': 'COMPLETED (FAILURE)',
      }]
  }

  invocation = invocation_pb2.Invocation(
      state=invocation_pb2.Invocation.FINALIZED,
      realm='devtools:ci',
      create_time=timestamp_pb2.Timestamp(seconds=1658269605),
      finalize_time=timestamp_pb2.Timestamp(seconds=1658269605),
  )

  def test_result(test_id, test_type):
    return test_result_pb2.TestResult(
        test_id=test_id,
        name=f'name {test_id}',
        expected=False,
        status=test_result_pb2.FAIL,
        tags=[ common_pb2.StringPair(
            key="test_type",
            value=test_type,
        )],
    )

  def resultdb_query(step_name, *results):
    return api.resultdb.query({
        'task-example.swarmingserver.appspot.com-some-task-id':
        api.resultdb.Invocation(
            proto=invocation,
            test_results=results,
        )},
        step_name=step_name,
    )

  def check_swarming_task_args(check, steps, step, *args):
    """Check that `arg` is passed to the command of the swarming task of a
    step.
    """
    check(step in steps)

    # A json string, configuring the task, is passed to the swarming command.
    check('-json-input' in steps[step].cmd)
    json_arg_index = steps[step].cmd.index('-json-input')
    check(len(steps[step].cmd) > json_arg_index)

    # The config must be valid json.
    json_input = json.loads(steps[step].cmd[json_arg_index + 1])

    # Dig down the structure to the wrapped command passed to the swarming
    # task.
    check(json_input)
    requests = json_input.get('requests', [])
    check(requests)
    task_slices = requests[0].get('task_slices', [])
    check(task_slices)
    check('properties' in task_slices[0])
    check('command' in task_slices[0]['properties'])
    command = task_slices[0]['properties']['command']
    check(command)

    # Ensure the argument is part of this command.
    for arg in args:
      check(arg in command)

  yield api.test(
      'failed parallel builder on E2E',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.step_data(
          'Pipeline E2E Tests.Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.post_process(MustRun, 'archive'),
      api.post_process(MustRun,
                       'Pipeline E2E Tests.Run tests.Trigger E2E Tests'),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      resultdb_query(
          'Pipeline E2E Tests.rdb query for e2e_tests',
          test_result('test/e2e/file1.test.ts:etest1', 'e2e_tests'),
          test_result('test/e2e/file2.test.ts:etest2', 'e2e_tests'),
      ),
      api.post_process(
          check_swarming_task_args,
          ('Pipeline E2E Tests.Flake exoneration attempt.'
           'Trigger E2E Tests (rerun).'
           '[trigger] E2E Tests (rerun) (Shard #0) on Ubuntu-22.04'),
          'test/e2e/file1.test.ts:etest1', 'test/e2e/file2.test.ts:etest2'),
      api.step_data(
          ('Pipeline E2E Tests.Flake exoneration attempt.E2E Tests (rerun).'
           'E2E Tests (rerun) shards '
           'results.E2E Tests (rerun) (Shard #0) on Ubuntu-22.04'),
          api.chromium_swarming.summary(None, data1),
      ),
      api.post_process(
          SummaryMarkdown, 'Failure in E2E Tests (shard #0), '
          'Failure in E2E Tests (rerun) (shard #0)'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      ('failed parallel builder on E2E with failed exoneration and '
       'passing flake detection'),
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.override_step_data(
          'find new tests.git diff',
          stdout=api.raw_io.output_text('test/e2e/file1_test.ts')),
      api.step_data(
          'Pipeline E2E Tests.Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.post_process(MustRun, 'archive'),
      api.post_process(MustRun,
                       'Pipeline E2E Tests.Run tests.Trigger E2E Tests'),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      resultdb_query(
          'Pipeline E2E Tests.rdb query for e2e_tests',
          test_result('test/e2e/file1.test.ts:etest1', 'e2e_tests'),
          test_result('test/e2e/file2.test.ts:etest2', 'e2e_tests'),
      ),
      api.post_process(
          check_swarming_task_args,
          ('Pipeline E2E Tests.Flake exoneration attempt.'
           'Trigger E2E Tests (rerun).'
           '[trigger] E2E Tests (rerun) (Shard #0) on Ubuntu-22.04'),
          'test/e2e/file1.test.ts:etest1', 'test/e2e/file2.test.ts:etest2'),
      api.step_data(
          ('Pipeline E2E Tests.Flake exoneration attempt.E2E Tests (rerun).'
           'E2E Tests (rerun) shards '
           'results.E2E Tests (rerun) (Shard #0) on Ubuntu-22.04'),
          api.chromium_swarming.summary(None, data1),
      ),
      api.post_process(
          SummaryMarkdown, 'Failure in E2E Tests (shard #0), '
          'Failure in E2E Tests (rerun) (shard #0)'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'failed parallel builder on E2E with exonerated tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.step_data(
          'Pipeline E2E Tests.Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.post_process(MustRun, 'archive'),
      api.post_process(MustRun,
                       'Pipeline E2E Tests.Run tests.Trigger E2E Tests'),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      resultdb_query(
          'Pipeline E2E Tests.rdb query for e2e_tests',
          test_result('test/e2e/file1.test.ts:test1', 'e2e_tests'),
          test_result('test/e2e/file2.test.ts:test2', 'e2e_tests'),
      ),
      api.post_process(
          SummaryMarkdown,
          'Flaky tests exonerated: test/e2e/file1.test.ts:test1, '
          'test/e2e/file2.test.ts:test2'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'failed parallel builder on E2E with exonerations limit reached',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.step_data(
          'Pipeline E2E Tests.Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.post_process(MustRun, 'archive'),
      api.post_process(MustRun,
                       'Pipeline E2E Tests.Run tests.Trigger E2E Tests'),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      resultdb_query(
          'Pipeline E2E Tests.rdb query for e2e_tests',
          *[
              test_result(f'test/e2e/file1.test.ts:test{i}', 'e2e_tests')
              for i in range(FLAKE_DETECTION_MAX_TESTS + 1)
          ],
      ),
      api.post_process(SummaryMarkdown,
                       'Failure in E2E Tests (shard #0), Too many failures'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  data = {
      'shards': [{
          "internal_failure": True,
      }]
  }
  yield api.test(
      'ci infra failure for parallel builder on unit tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.step_data(
          'Pipeline Unit Tests.Run tests.Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(SummaryMarkdown,
                       'Infra Failure in Unit Tests (shard #0)'),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      api.post_process(DropExpectation),
      status='INFRA_FAILURE',
  )

  data = {
      'shards': [{
          "internal_failure": True,
      }]
  }
  yield api.test(
      'ci infra failure for parallel builder on performance tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.properties(perf_benchmarks=True),
      api.step_data(('Pipeline Performance Tests.Run tests.Performance Tests.'
                     'Performance Tests shards results.'
                     'Performance Tests (Shard #0) on Ubuntu-22.04'),
                    api.chromium_swarming.summary(None, data)),
      api.post_process(SummaryMarkdown,
                       'Infra Failure in Performance Tests (shard #0)'),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(
          MustRun, 'Pipeline Performance Tests.Run tests.Performance Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      api.post_process(DropExpectation),
      status='INFRA_FAILURE',
  )

  data = {
      'shards': [{
          'state': 'COMPLETED (FAILURE)',
      }]
  }
  yield api.test(
      'ci failed parallel builder on unit tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.step_data(
          'Pipeline Unit Tests.Run tests.Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(
          SummaryMarkdown,
          'Failure in Unit Tests (shard #0), Failure in Unit Tests (rerun) '
          '(shard #0)'),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      resultdb_query(
          'Pipeline Unit Tests.rdb query for unit_tests',
          test_result('unit1', 'unit_tests'),
          test_result('unit2', 'unit_tests'),
      ),
      api.step_data(
          ('Pipeline Unit Tests.Flake exoneration attempt.'
           'Unit Tests (rerun).Unit Tests (rerun) shards results.'
           'Unit Tests (rerun) (Shard #0) on Ubuntu-22.04'),
          api.chromium_swarming.summary(None, data),
      ),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'ci failed parallel builder on unit tests with exonerated tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.step_data(
          'Pipeline Unit Tests.Run tests.Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(SummaryMarkdown, 'Flaky tests exonerated: unit1, unit2'),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      resultdb_query(
          'Pipeline Unit Tests.rdb query for unit_tests',
          test_result('unit1', 'unit_tests'),
          test_result('unit2', 'unit_tests'),
      ),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )
  yield api.test(
      ('ci failed parallel builder on unit tests with failed '
       'exoneration and passing flake detection'),
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.override_step_data(
          'find new tests.git diff',
          stdout=api.raw_io.output_text('front_end/foo.test.ts')),
      api.step_data(
          'Pipeline Unit Tests.Run tests.Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(
          SummaryMarkdown,
          'Failure in Unit Tests (shard #0), Failure in Unit Tests (rerun) '
          '(shard #0)'),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      resultdb_query(
          'Pipeline Unit Tests.rdb query for unit_tests',
          test_result('unit1', 'unit_tests'),
          test_result('unit2', 'unit_tests'),
      ),
      api.step_data(
          ('Pipeline Unit Tests.Flake exoneration attempt.'
           'Unit Tests (rerun).Unit Tests (rerun) shards results.'
           'Unit Tests (rerun) (Shard #0) on Ubuntu-22.04'),
          api.chromium_swarming.summary(None, data),
      ),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'ci failed parallel builder on node unit tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.step_data(('Pipeline Unit Tests (node).Run tests.Unit Tests (node).'
                     'Unit Tests (node) shards results.'
                     'Unit Tests (node) (Shard #0) on Ubuntu-22.04'),
                    api.chromium_swarming.summary(None, data)),
      api.post_process(
          SummaryMarkdown, 'Failure in Unit Tests (node) (shard #0), '
          'Failure in Unit Tests (node) (rerun) (shard #0)'),
      api.post_process(
          MustRun, 'Pipeline Unit Tests (node).Run tests.Unit Tests (node)'),
      resultdb_query(
          'Pipeline Unit Tests (node).rdb query for node_unit_tests',
          test_result('node_unit1', 'node_unit_tests'),
      ),
      api.step_data(
          ('Pipeline Unit Tests (node).Flake exoneration attempt.'
           'Unit Tests (node) (rerun).Unit Tests (node) (rerun) shards '
           'results.Unit Tests (node) (rerun) (Shard #0) on Ubuntu-22.04'),
          api.chromium_swarming.summary(None, data),
      ),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'ci failed parallel builder on node unit tests with exonerated tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.step_data(('Pipeline Unit Tests (node).Run tests.Unit Tests (node).'
                     'Unit Tests (node) shards results.'
                     'Unit Tests (node) (Shard #0) on Ubuntu-22.04'),
                    api.chromium_swarming.summary(None, data)),
      api.post_process(SummaryMarkdown, 'Flaky tests exonerated: node_unit1'),
      api.post_process(
          MustRun, 'Pipeline Unit Tests (node).Run tests.Unit Tests (node)'),
      resultdb_query(
          'Pipeline Unit Tests (node).rdb query for node_unit_tests',
          test_result('node_unit1', 'node_unit_tests'),
      ),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )
  yield api.test(
      ('ci failed parallel builder on node unit tests with failed '
       'exoneration and passing flake detection'),
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.override_step_data(
          'find new tests.git diff',
          stdout=api.raw_io.output_text('front_end/foo.test.ts')),
      api.step_data(('Pipeline Unit Tests (node).Run tests.Unit Tests (node).'
                     'Unit Tests (node) shards results.'
                     'Unit Tests (node) (Shard #0) on Ubuntu-22.04'),
                    api.chromium_swarming.summary(None, data)),
      api.post_process(
          SummaryMarkdown, 'Failure in Unit Tests (node) (shard #0), '
          'Failure in Unit Tests (node) (rerun) (shard #0)'),
      api.post_process(
          MustRun, 'Pipeline Unit Tests (node).Run tests.Unit Tests (node)'),
      resultdb_query(
          'Pipeline Unit Tests (node).rdb query for node_unit_tests',
          test_result('node_unit1', 'node_unit_tests'),
      ),
      api.step_data(
          ('Pipeline Unit Tests (node).Flake exoneration attempt.'
           'Unit Tests (node) (rerun).Unit Tests (node) (rerun) shards '
           'results.Unit Tests (node) (rerun) (Shard #0) on Ubuntu-22.04'),
          api.chromium_swarming.summary(None, data),
      ),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'ci failed parallel builder on unit tests karma file copy',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.step_data(('Pipeline Unit Tests.Run tests.Unit Tests.'
                     'copy unit tests coverage data'),
                    api.file.errno('WinError 3')),
      api.post_process(SummaryMarkdown,
                       'Failed in post collect for Unit Tests'),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      api.post_process(DropExpectation),
      status='INFRA_FAILURE',
  )
  yield api.test(
      'ci failed parallel builder on Performance tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.properties(perf_benchmarks=True),
      api.step_data(('Pipeline Performance Tests.Run tests.Performance Tests.'
                     'Performance Tests shards results.'
                     'Performance Tests (Shard #0) on Ubuntu-22.04'),
                    api.chromium_swarming.summary(None, data)),
      api.post_process(SummaryMarkdown,
                       'Failure in Performance Tests (shard #0)'),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(
          MustRun, 'Pipeline Performance Tests.Run tests.Performance Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  data1 = {
      'shards': [{
          'state': 'COMPLETED (FAILURE)',
      }]
  }
  yield api.test(
      'ci failed parallel builder on unit, E2E and performance tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.properties(perf_benchmarks=True),
      api.step_data(
          'Pipeline Unit Tests.Run tests.Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(('Pipeline Performance Tests.Run tests.Performance Tests.'
                     'Performance Tests shards results.'
                     'Performance Tests (Shard #0) on Ubuntu-22.04'),
                    api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'Pipeline E2E Tests.Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.post_process(
          SummaryMarkdown,
          ('Failure in Unit Tests (shard #0), '
           'Failure in E2E Tests (shard #0), '
           'Failure in Performance Tests (shard #0)'),
      ),
      api.post_process(MustRun, 'Pipeline Unit Tests.Run tests.Unit Tests'),
      api.post_process(
          MustRun, 'Pipeline Performance Tests.Run tests.Performance Tests'),
      api.post_process(MustRun, 'Pipeline E2E Tests.Run tests.E2E Tests'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'ci failed parallel builder on API tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.step_data(
          'Pipeline API Tests.Run tests.API Tests.API Tests ' +
          'shards results.API Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.post_process(
          SummaryMarkdown,
          'Failure in API Tests (shard #0), Failure in API Tests (rerun) '
          '(shard #0)'),
      api.post_process(MustRun, 'Pipeline API Tests.Run tests.API Tests'),
      resultdb_query(
          'Pipeline API Tests.rdb query for api_tests',
          test_result('api1', 'api_tests'),
      ),
      api.step_data(
          ('Pipeline API Tests.Flake exoneration attempt.'
           'API Tests (rerun).API Tests (rerun) shards results.'
           'API Tests (rerun) (Shard #0) on Ubuntu-22.04'),
          api.chromium_swarming.summary(None, data1),
      ),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'ci flake detection execution',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.override_step_data(
          'find new tests.git diff',
          stdout=api.raw_io.output_text(
              'front_end/foo.test.ts\ntest/e2e/bar_test.ts')),
      api.post_process(
          MustRun,
          ('Pipeline Unit Tests.Detect flakes in new tests.'
           'Trigger Unit Tests (flake detection)'),
      ),
      api.post_process(
          MustRun,
          ('Pipeline E2E Tests.Detect flakes in new tests.'
           'Trigger E2E Tests (flake detection)'),
      ),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'ci flake detection failure',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.override_step_data(
          'find new tests.git diff',
          stdout=api.raw_io.output_text('front_end/foo.test.ts')),
      api.step_data(('Pipeline Unit Tests.Detect flakes in new tests.'
                     'Unit Tests (flake detection).'
                     'Unit Tests (flake detection) shards results.'
                     'Unit Tests (flake detection) (Shard #0) on Ubuntu-22.04'),
                    api.chromium_swarming.summary(None, data1)),
      api.post_process(SummaryMarkdown,
                       'Failure in Unit Tests (flake detection) (shard #0)'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
