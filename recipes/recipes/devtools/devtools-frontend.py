# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from google.protobuf import timestamp_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
    common as common_pb2,  # go/pyformat-break
    invocation as invocation_pb2,  #
    test_result as test_result_pb2,  #
)
from recipe_engine.post_process import (DoesNotRun, DropExpectation, Filter,
                                        MustRun, SummaryMarkdown)
from recipe_engine.recipe_api import Property
from RECIPE_MODULES.build.devtools.commons import SwarmingTrigger
from RECIPE_MODULES.build.devtools.test_runner_base import FLAKE_DETECTION_MAX_TESTS
from RECIPE_MODULES.build.devtools.e2e_tests_runner import E2ETests, E2ETestDivider
from RECIPE_MODULES.build.devtools.interactions_tests_runner import InteractionsTests
from RECIPE_MODULES.build.devtools.performance_tests_runner import PerformanceTests
from RECIPE_MODULES.build.devtools.test_phases import FirstRunPhase, ExonerationPhase
from RECIPE_MODULES.build.devtools.unit_tests_runner import UnitTests


DEPS = [
    'builder_group',
    'chromium',
    'chromium_swarming',
    'devtools',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/git',
    'depot_tools/tryserver',
    'perf_dashboard',
    'recipe_engine/buildbucket',
    'recipe_engine/cas',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/random',
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'recipe_engine/time',
    'depot_tools/gsutil',
]

PROPERTIES = {
    'builder_config':
        Property(
            kind=str,
            help='Configuration name for the builder (Debug/Release)',
            default='Release'),
    'is_official_build':
        Property(
            kind=bool,
            help='Turn the is_official_build gn flag on (default off)',
            default=False),
    'devtools_skip_typecheck':
        Property(
            kind=bool,
            help='Turn the devtools_skip_typecheck gn flag on (default off)',
            default=False),
    'clobber':
        Property(
            kind=bool,
            help='Should the builder clean up the out/ folder before building',
            default=False),
    'coverage':
        Property(
            kind=bool, help='Should the runner have coverage', default=True),
    'perf_benchmarks':
        Property(
            kind=bool,
            help='Run DevTools performance benchmarks',
            default=False),
}

def RunSteps(api, builder_config, is_official_build, devtools_skip_typecheck,
             clobber, coverage, perf_benchmarks):
  api.devtools.configure(builder_config, is_official_build,
                         devtools_skip_typecheck)
  update_result = api.devtools.update()

  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  with api.devtools.depot_on_path(source_dir):
    api.devtools.clean_out_dir(source_dir, builder_config, clobber)
    api.chromium.run_gn(source_dir, build_dir)

    compilation_result = api.chromium.compile(source_dir, build_dir)
    if compilation_result.status != common_pb.SUCCESS:
      return compilation_result
    cas_digest = api.devtools.archive_to_cas(source_dir)

    divider = E2ETestDivider(api, source_dir, builder_config)
    trigger = SwarmingTrigger(api, cas_digest)
    tests = [
        UnitTests(api, source_dir, trigger, builder_config, coverage,
                  'Unit Tests'),
        InteractionsTests(api, source_dir, trigger, builder_config, coverage,
                          'Interactions Tests'),
        E2ETests(api, source_dir, trigger, builder_config, coverage,
                 'E2E Tests', divider),
        PerformanceTests(api, source_dir, trigger, builder_config, coverage,
                         'Performance Tests'),
    ]
    tests = [t for t in tests if not t.skip()]

    FirstRunPhase(api).run_all(
        tests,
        task_on_builder=lambda: run_lint_check(api, builder_config, source_dir))

    results = ExonerationPhase(api).run_all(tests)

    publish_coverage_points(api, source_dir, skip=not coverage)
    publish_performance_benchmarks(api, source_dir, skip=not perf_benchmarks)

    return results.raw_result()



def lint_script_exists(api, source_dir, name):
  script_file = source_dir / 'scripts/test' / name
  return api.path.exists(script_file)


def run_lint_check(api, builder_config, source_dir):
  if not api.devtools.is_debug(builder_config):
    with api.step.nest('Linting'):
      lint_script = 'run_lint_check_js.mjs'
      if not lint_script_exists(api, source_dir, lint_script):
        lint_script = 'run_lint_check_js.js'
      api.devtools.run_node_script(source_dir, 'Lint Check with ESLint',
                                   lint_script)
      api.devtools.run_node_script(source_dir, 'Lint check with Stylelint',
                                   'run_lint_check_css.js')


def publish_performance_benchmarks(api, source_dir, skip):
  if skip:
    return
  report_file = source_dir / 'perf-data/devtools-perf.json'
  front_end_results = api.file.read_json('Read performance data results',
                                         report_file)
  tmp_dir = api.m.path.mkdtemp('perf-results')
  results_file = tmp_dir / 'devtools-perf.json'
  git_revision = api.bot_update.last_returned_properties['got_revision']
  api.m.file.write_json(
      'Write Skia Perf format', results_file, {
          'version': 1,
          'git_hash': git_revision,
          'key': {
              'master': 'client.devtools-frontend.integration',
              'bot': api.m.buildbucket.builder_name
          },
          'results': front_end_results
      })
  save_perf_data_in_bucket(api, results_file)


def save_perf_data_in_bucket(api, report_file):
  bucket = 'devtools-frontend-perf'
  today = api.m.time.utcnow().strftime('%Y/%m/%d/%H')
  upload_name = '{}/{}/{}/{}/{}/{}'.format(
      'ingest', today, 'client.devtools-frontend.integration',
      api.m.buildbucket.builder_name, 'performance-tests', 'perf-data.json')
  api.m.step.empty(f'Upload name {upload_name}')
  api.m.gsutil.upload(
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


def publish_coverage_points(api, source_dir, skip):
  if api.tryserver.is_tryserver or skip:
    return
  with api.step.nest('Coverage'):

    api.devtools.run_node_script(source_dir, 'Combining coverage reports',
                                 'merge_coverage_reports.js')

    dimensions = ["lines", "statements", "functions", "branches"]

    report_file = source_dir / 'karma-coverage/coverage-summary.json'
    summary = api.file.read_json(
        'Coverage summary', report_file, test_data=test_cov_data())
    totals = summary['total']
    api.step.active_result.presentation.step_text = "".join([
        "\n%s: %s%%" % (dim.capitalize(), totals[dim]['pct'])
        for dim in dimensions
    ])

    with api.context(cwd=source_dir):
      git_revision = api.bot_update.last_returned_properties['got_revision']

      commit_count = api.git(
          'rev-list',
          '--count',
          git_revision,
          name='Retrieve commit count',
          stdout=api.raw_io.output_text(),
          step_test_data=lambda: api.raw_io.test_api.stream_output_text('123\n')
      ).stdout.strip()

    points = [
        _point(api, dim, summary['total'], commit_count) for dim in dimensions
    ]
    #TODO(liviurau) find another way arroud 400 error "Invalid ID (revision) 1055;
    #compared to previous ID 0, it was larger or smaller by too much."
    api.perf_dashboard.add_point(points, halt_on_failure=False)


def _point(api, dimension, totals, commit_count):
  p = {
      'master': api.m.builder_group.for_current,
      'bot': api.m.buildbucket.builder_name,
      'test': '/'.join(['devtools.infra', 'coverage_v2', dimension]),
      'revision': int(commit_count),
      'value': totals[dimension]['pct'],
      'masterid': api.m.builder_group.for_current,
      'buildername': api.m.buildbucket.builder_name,
      'buildnumber': api.m.buildbucket.build.number,
  }

  p['supplemental_columns'] = {
      'a_default_rev': 'r_devtools_git',
      'r_devtools_git': api.bot_update.last_returned_properties['got_revision'],
  }
  return p


def GenTests(api):
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
      api.path.exists(
          api.path.cache_dir /
          'builder/devtools-frontend/karma-coverage/coverage-summary.json',),
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
      'skip typecheck build',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(devtools_skip_typecheck=True, builder_config='Debug'),
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
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.post_process(MustRun, 'archive'),
      api.post_process(MustRun, 'Run tests.Trigger Tests.Trigger E2E Tests'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'ci parallel builder performance benchmarks',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.properties(perf_benchmarks=True),
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.post_process(MustRun, 'archive'),
      api.post_process(MustRun, 'Run tests.Trigger Tests.Trigger Unit Tests'),
      api.post_process(MustRun,
                       'Run tests.Trigger Tests.Trigger Interactions Tests'),
      api.post_process(MustRun, 'Run tests.Trigger Tests.Trigger E2E Tests'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.Performance Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      api.post_process(
          Filter().include_re('Run tests.Trigger Tests.*|.*\(Shard #\d*\).*')),
      status='SUCCESS',
  )
  yield api.test(
      'legacy ci parallel builder performance benchmarks',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.properties(perf_benchmarks=True, branch_number=1111),
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.post_process(MustRun, 'archive'),
      api.post_process(MustRun, 'Run tests.Trigger Tests.Trigger Unit Tests'),
      api.post_process(MustRun,
                       'Run tests.Trigger Tests.Trigger Interactions Tests'),
      api.post_process(MustRun, 'Run tests.Trigger Tests.Trigger E2E Tests'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.Performance Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      api.post_process(
          Filter().include_re('Run tests.Trigger Tests.*|.*\(Shard #\d*\).*')),
      status='SUCCESS',
  )

  data1 = {
      'shards': [{
          'state': 'COMPLETED (FAILURE)',
      }]
  }
  data2 = {
      'shards': [{
          'state': 'COMPLETED (SUCCESS)',
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

  yield api.test(
      'failed parallel builder on E2E',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.step_data(
          'Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #1) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data2)),
      api.post_process(MustRun, 'archive'),
      api.post_process(MustRun, 'Run tests.Trigger Tests.Trigger E2E Tests'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      resultdb_query(
          'Flake exonaration attempt.rdb query',
          test_result('e2e/file1: etest1', 'e2e_tests'),
          test_result('e2e/file2: etest2', 'e2e_tests'),
      ),
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.step_data(
          'Flake exonaration attempt.E2E Tests (rerun).E2E Tests (rerun) shards'
          ' results.E2E Tests (rerun) (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1),
      ),
      api.post_process(
          SummaryMarkdown,
          'Failure in E2E Tests (shard #0), Failure in E2E Tests (shard #1), '
          'Failure in E2E Tests (rerun) (shard #0)'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'failed parallel builder on E2E with exonerated tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.step_data(
          'Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #1) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data2)),
      api.post_process(MustRun, 'archive'),
      api.post_process(MustRun, 'Run tests.Trigger Tests.Trigger E2E Tests'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      resultdb_query(
          'Flake exonaration attempt.rdb query',
          test_result('e2e/file1: e/test/1', 'e2e_tests'),
          test_result('e2e/file2: e/test/2', 'e2e_tests'),
      ),
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.post_process(
          SummaryMarkdown,
          'Flaky tests exonerated: Failure in E2E Tests (shard #0), Failure in'
          ' E2E Tests (shard #1)'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'failed parallel builder on E2E with exonerations limit reached',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.step_data(
          'Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #1) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data2)),
      api.post_process(MustRun, 'archive'),
      api.post_process(MustRun, 'Run tests.Trigger Tests.Trigger E2E Tests'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      resultdb_query(
          'Flake exonaration attempt.rdb query',
          *[
              test_result(f'e2e/file1: e/test/{i}', 'e2e_tests')
              for i in range(FLAKE_DETECTION_MAX_TESTS + 1)
          ],
      ),
      api.post_process(
          DoesNotRun, 'Flake exonaration attempt.'
          'Trigger E2E Tests (rerun).Read test list'),
      api.post_process(
          SummaryMarkdown,
          'Failure in E2E Tests (shard #0), Failure in E2E Tests (shard #1), '
          'Too many failures'),
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
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.step_data(
          'Run tests.Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(SummaryMarkdown,
                       'Infra Failure in Unit Tests (shard #0)'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
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
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.step_data(
          'Run tests.Performance Tests.Performance Tests ' +
          'shards results.Performance Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(SummaryMarkdown,
                       'Infra Failure in Performance Tests (shard #0)'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.Performance Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      api.post_process(DropExpectation),
      status='INFRA_FAILURE',
  )

  data = {
      'shards': [{
          "internal_failure": True,
      }]
  }
  yield api.test(
      'cq infra failure for parallel builder on interactions',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.step_data(
          'Run tests.Interactions Tests.Interactions Tests shards ' +
          'results.Interactions Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(SummaryMarkdown, 'Infra Failure in Interactions Tests'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      api.post_process(DropExpectation),
      status='INFRA_FAILURE',
  )

  data = {
      'shards': [{
          'state': 'COMPLETED (FAILURE)',
      }]
  }
  yield api.test(
      'cq failed parallel builder on interactions',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.step_data(
          'Run tests.Interactions Tests.Interactions Tests shards ' +
          'results.Interactions Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(
          SummaryMarkdown,
          'Failure in Interactions Tests, Failure in Interactions '
          'Tests (rerun)'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      resultdb_query(
          'Flake exonaration attempt.rdb query',
          test_result('interactions/file1: unit1', 'interactions_tests'),
          test_result('interactions/file2: unit2', 'interactions_tests'),
      ),
      api.step_data(
          'Flake exonaration attempt.Interactions Tests (rerun).Interactions '
          'Tests (rerun) shards results.Interactions Tests (rerun) (Shard #0) '
          'on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data),
      ),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  data = {
      'shards': [{
          'state': 'COMPLETED (FAILURE)',
      }]
  }
  yield api.test(
      'ci failed parallel builder on interactions',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.step_data(
          'Run tests.Interactions Tests.Interactions Tests shards ' +
          'results.Interactions Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(SummaryMarkdown,
                       'Failure in Interactions Tests (shard #0)'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      api.post_process(DropExpectation),
      status='FAILURE',
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
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.step_data(
          'Run tests.Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(
          SummaryMarkdown,
          'Failure in Unit Tests (shard #0), Failure in Unit Tests (rerun) '
          '(shard #0)'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      resultdb_query(
          'Flake exonaration attempt.rdb query',
          test_result('unit1', 'unit_tests'),
          test_result('unit2', 'unit_tests'),
      ),
      api.step_data(
          'Flake exonaration attempt.Unit Tests (rerun).Unit Tests (rerun) '
          'shards results.Unit Tests (rerun) (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data),
      ),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'legacy ci failed parallel builder on unit tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      api.properties(branch_number=1111),
      ci_build(builder='parallel_linux'),
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.step_data(
          'Run tests.Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(
          SummaryMarkdown,
          'Failure in Unit Tests (shard #0), Failure in Unit Tests (rerun) '
          '(shard #0)'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      resultdb_query(
          'Flake exonaration attempt.rdb query',
          test_result('unit1', 'unit_tests'),
          test_result('unit2', 'unit_tests'),
      ),
      api.step_data(
          'Flake exonaration attempt.Unit Tests (rerun).Unit Tests (rerun) '
          'shards results.Unit Tests (rerun) (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data),
      ),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'ci failed parallel builder on unit tests karma file copy',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.step_data('Run tests.Unit Tests.copy unit tests coverage data',
                    api.file.errno('WinError 3')),
      api.post_process(SummaryMarkdown,
                       'Failed in post collect for Unit Tests'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      api.post_process(DropExpectation),
      status='INFRA_FAILURE',
  )
  yield api.test(
      'ci failed parallel builder on Performance tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.properties(perf_benchmarks=True),
      api.step_data(
          'Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
          api.raw_io.stream_output_text(
              'test1\ntest2\ntest3\ntest4\n', stream='stdout')),
      api.step_data(
          'Run tests.Performance Tests.Performance Tests ' +
          'shards results.Performance Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(SummaryMarkdown,
                       'Failure in Performance Tests (shard #0)'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.Performance Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  data1 = {
      'shards': [{
          'state': 'COMPLETED (FAILURE)',
      }]
  }
  data2 = {
      'shards': [{
          'state': 'COMPLETED (SUCCESS)',
      }]
  }
  yield api.test(
      'ci failed parallel builder on unit, interactions, E2E and' +
      ' performance tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.properties(perf_benchmarks=True),
      api.step_data('Run tests.Trigger Tests.Trigger E2E Tests.Read test list',
                    api.file.read_text('test1\ntest2\ntest3\ntest4\ntest4\n')),
      api.step_data(
          'Run tests.Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'Run tests.Interactions Tests.Interactions Tests shards ' +
          'results.Interactions Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'Run tests.Performance Tests.Performance Tests shards ' +
          'results.Performance Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'Run tests.E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #1) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data2)),
      api.post_process(
          SummaryMarkdown,
          'Failure in Unit Tests (shard #0), Failure in Interactions Tests '
          '(shard #0), Failure in E2E Tests (shard #0), Failure in E2E Tests '
          '(shard #1), Failure in Performance Tests (shard #0)'),
      api.post_process(MustRun, 'Run tests.Unit Tests'),
      api.post_process(MustRun, 'Run tests.Interactions Tests'),
      api.post_process(MustRun, 'Run tests.Performance Tests'),
      api.post_process(MustRun, 'Run tests.E2E Tests'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )
