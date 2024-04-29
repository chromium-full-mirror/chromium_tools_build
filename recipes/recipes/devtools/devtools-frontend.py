# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from abc import ABC, abstractmethod
from collections import defaultdict
from functools import cached_property
from google.protobuf import timestamp_pb2
from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  common as common_pb2,  # go/pyformat-break
  invocation as invocation_pb2,  #
  resultdb as resultdb_pb2,  #
  test_result as test_result_pb2,  #
)
from recipe_engine import post_process
from recipe_engine.recipe_api import Property
from recipe_engine.recipe_api import InfraFailure, StepFailure

import re

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
    'recipe_engine/raw_io',
    'recipe_engine/resultdb',
    'recipe_engine/step',
    'recipe_engine/swarming',
    'recipe_engine/time',
    'depot_tools/gsutil',
    'v8_orchestrator',
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

FLAKE_DETECTION_MAX_TESTS = 20

class Results():

  def __init__(self, infra_failures=None, task_failures=None,
               exonerated_failures=None):
    self.infra_failures = infra_failures or []
    self.task_failures = task_failures or []
    self.exonerated_failures = exonerated_failures or []

  def __add__(self, result):
    return Results(
        self.infra_failures + result.infra_failures,
        self.task_failures + result.task_failures,
        self.exonerated_failures + result.exonerated_failures,
    )

  def exonerable(self):
    """True if the result contains only test failures.
    Infra failures are not exonerable."""
    return bool(self.task_failures and not self.infra_failures)

  def can_exonerate(self):
    """True if this result can exonerate other results."""
    return not bool(self.task_failures or self.infra_failures)

  def add_infra_failure(self, failure):
    self.infra_failures.append(failure)

  def add_test_failure(self, failure):
    self.task_failures.append(failure)

  def raise_on_failure(self):
    """
    Prioritize test failures in order to be able to close the tree even if we
    have infra failures.
    """
    if self.task_failures:
      raise StepFailure(', '.join(self.task_failures))

    if self.infra_failures:
      raise InfraFailure(', '.join(self.infra_failures))

  def raw_result(self):
    self.raise_on_failure()
    summary = None
    if self.exonerated_failures:
      summary = 'Flaky tests exonerated: ' + ', '.join(self.exonerated_failures)
    return result_pb2.RawResult(
        summary_markdown=summary,
        status=common_pb.SUCCESS,
    )

class NoOpExonerator:
  def __init__(self, runner):
    self.runner = runner
    self.api = runner.api
    # Used to indicate that no task was triggered; may contain a failure if the
    # reason for not triggering qualifies as such
    self.skip_result = None

  def trigger(self, test_names):
    pass

  def process_results(self):
    pass

class RerunExonerator(NoOpExonerator):
  def trigger(self, test_names):
    owned_tests = test_names.get(self.runner.test_type_tag)
    if not owned_tests:
      self.skip_result = Results()
      return
    if len(owned_tests) > FLAKE_DETECTION_MAX_TESTS:
      self.skip_result = Results()
      self.skip_result.add_test_failure('Too many failures')
      self.api.step.empty('Too many tests to check for flakes')
      return
    self.runner.step_name += ' (rerun)'
    self.runner.prepare_filtered_rerun(owned_tests)
    self.runner.trigger()

  def process_results(self):
    if self.skip_result:
      self.runner.results += self.skip_result
      return
    self.runner.process_results()

class DevToolsTests(ABC):

  def __init__(self, api, cas_digest, builder_config, coverage, step_name):
    self.api = api
    self.cas_digest = cas_digest
    self.builder_config = builder_config
    self.step_name = step_name
    self.output_dir = self.api.path.mkdtemp()
    self.tasks = []
    self.coverage = coverage
    self.env = {}
    self.extra_args = []
    # Used to accumulate results from the rerun and the original run
    self.results = Results()

  @property
  @abstractmethod
  def test_type_tag(self):
    """
    The tag that identifies the type of tests we are running. This is used to
    identify the tests that we want to rerun in case of flakiness.
    """

  @cached_property
  def exonerator(self):
    return RerunExonerator(self)

  def skip(self):
    return False

  def collect(self):
    """
    Returns a Results object that contains a list of the infra failures and
    another one for test failures (empty lists if there are no failures).
    """
    results = Results()
    with self.api.step.nest(f'{self.step_name} shards results'):
      for i in range(len(self.tasks)):
        step, is_valid = self.api.chromium_swarming.collect_task(self.tasks[i])
        if not is_valid:
          results.add_infra_failure(
              f'Infra Failure in {self.step_name} (shard #{i})')
        elif step.presentation.status != self.api.step.SUCCESS:
          results.add_test_failure(f'Failure in {self.step_name} (shard #{i})')

    return results

  def include_invocations(self):
    for task in self.tasks:
      # Remove 'invocations/' because it is added again in include_invocations.
      self.api.resultdb.include_invocations(
        [i[len('invocations/'):] for i in task.get_invocation_names()])

  def prepare_filtered_rerun(self, test_names):
    self.extra_args = [
      f'--mocha-grep="{self.test_names_to_grep_string(test_names)}"',
    ]

  def test_names_to_grep_string(self, names):
    return '|'.join([
        self.test_name_to_grep_string(name) for name in names
      ])

  def test_name_to_grep_string(self, name):
    return name.replace('/', ' ')

  def trigger(self):
    with self.api.step.nest(f'Trigger {self.step_name}'):
      self.tasks = self.api.devtools.trigger_test_swarming_tasks(
          step_name=self.step_name,
          cas_digest=self.cas_digest,
          task_output_dir=self.output_dir,
          rdb_test_type=self.test_type_tag,
          commands=self.construct_commands(),
          env=self.construct_env(),
      )

  def process_results(self):
    new_results = self._process_results()
    if self.results.exonerable() and new_results.can_exonerate():
      new_results.exonerated_failures = self.results.task_failures
      self.results.task_failures = []
    self.results += new_results

  @abstractmethod
  def _process_results(self):
    """
    Collects the tasks with '_collect_tasks' and does any extra things we want
    to do after the collection. Returns a list of the failures as strings
    (empty list if there are no failures).
    """

  def trigger_exoneration(self, test_names):
    self.exonerator.trigger(test_names)

  def process_exoneration_results(self):
    self.exonerator.process_results()

  def construct_env(self):
    return self.env

  @abstractmethod
  def construct_commands(self):
    """
    Returns a list of commands to be run in the swarming tasks.
    """


class UnitTests(DevToolsTests):
  @property
  def test_type_tag(self):
    return 'unit_tests'

  def construct_commands(self):
    # TODO(liviurau) Use shuffle on unit tests after runner fix.
    shuffle = []  #['--shuffle'] if self.api.devtools.is_shuffled_run() else []
    command = [
        self.api.path.join('scripts', 'test', 'run_unittests.py'),
        '--target=' + self.builder_config,
        '--swarming-output-file',
        '${ISOLATED_OUTDIR}',
    ]
    if self.coverage:
      command.append('--coverage')
    return [command + shuffle]

  def _process_results(self):
    with self.api.step.nest(self.step_name):
      result = self.collect()
      if not result.infra_failures:
        if self.coverage:
          try:
            self.copy_coverage_data()
          except self.api.step.StepFailure:
            result.add_infra_failure(
                f'Failed to copy coverage data from {self.step_name}')
      return result

  def copy_coverage_data(self):
    shard_output_dir = self.tasks[0].get_task_shard_output_dirs()[0]
    coverage_data_dir = self.output_dir / shard_output_dir / 'karma-coverage'
    self.api.file.rmtree(
        'remove coverage files if they exist',
        self.api.path.join(self.api.path.checkout_dir, 'karma-coverage'))
    self.api.file.copytree(
        'copy unit tests coverage data', coverage_data_dir,
        self.api.path.join(self.api.path.checkout_dir, 'karma-coverage'))

  def prepare_filtered_rerun(self, test_names):
    self.env = {
      'MOCHA_FGREP': self.test_names_to_grep_string(test_names),
    }



class InteractionsTests(DevToolsTests):
  @property
  def test_type_tag(self):
    return 'interactions_tests'

  def __init__(self,
               api,
               cas_digest,
               builder_config,
               coverage,
               step_name,
               bucket='devtools-frontend-screenshots'):
    self.bucket = bucket
    super().__init__(api, cas_digest, builder_config, coverage, step_name)

  def collect(self):
    if self.api.tryserver.is_tryserver:
      with self.api.step.nest(f'{self.step_name} shards results') \
        as presentation:
        self.api.chromium_swarming.collect_task(self.tasks[0])
      if presentation.status != self.api.step.SUCCESS:
        if presentation.status == self.api.step.EXCEPTION:
          return Results(infra_failures=[f'Infra Failure in {self.step_name}'])
        return Results(task_failures=[f'Failure in {self.step_name}'])
      return Results()

    return super().collect()

  def construct_commands(self):
    command = [
          self.api.path.join('third_party', 'node', 'node.py'),
          "--output",
          self.api.path.join('scripts', 'test', 'run_test_suite.js'),
          "--test-suite-path=gen/test/interactions",
          "--test-suite-source-dir=test/interactions",
          "--test-server-type='component-docs'",
          "--target=" + self.builder_config,
          '--swarming-output-file',
          '${ISOLATED_OUTDIR}',
      ] + self.extra_args
    if self.coverage:
      command.append('--coverage')
    return [command]

  def construct_env(self):
    env = {
          "FORCE_UPDATE_ALL_GOLDENS": 'True',
          "THROW_AFTER_GOLDENS_UPDATE": 'True',
          "HTML_OUTPUT_FILE": self.api.path.join(
              '${ISOLATED_OUTDIR}',
              'interactions_failure_screenshots.html'
          ),
      }
    env.update(self.env)
    return env

  def _process_results(self):
    with self.api.step.nest(self.step_name):
      with self.api.devtools.collect_screenshots_on_trybot(self.bucket):
        result = self.collect()
        if not result.infra_failures:
          if self.coverage:
            self.copy_coverage_data()
          self.copy_golden_snapshots()
      return result

  def copy_coverage_data(self):
    shard_output_dir = self.tasks[0].get_task_shard_output_dirs()[0]
    coverage_data_dir = (
        self.output_dir / shard_output_dir / 'interactions-coverage')
    self.api.file.rmtree(
        'remove coverage files if they exist',
        self.api.path.join(self.api.path.checkout_dir, 'interactions-coverage'))
    self.api.file.copytree(
        'copy interaction tests coverage data', coverage_data_dir,
        self.api.path.join(self.api.path.checkout_dir, 'interactions-coverage'))

  def copy_golden_snapshots(self):
    shard_output_dir = self.tasks[0].get_task_shard_output_dirs()[0]
    golden_snapshots_dir = self.output_dir / shard_output_dir / 'goldens'
    self.api.file.rmtree(
        'remove previous goldens',
        self.api.path.join(self.api.path.checkout_dir, 'test', 'interactions',
                           'goldens'))
    self.api.file.copytree(
        'copy golden snapshots', golden_snapshots_dir,
        self.api.path.join(self.api.path.checkout_dir, 'test', 'interactions',
                           'goldens'))

  def test_name_to_grep_string(self, name):
    name = re.sub(r'^interactions/.*: ', '', name)
    return super().test_name_to_grep_string(name)

class E2ETests(DevToolsTests):
  @property
  def test_type_tag(self):
    prefix = 'shuffled_' if self.api.devtools.is_shuffled_run() else ''
    return prefix + 'e2e_tests'

  def __init__(self, api, cas_digest, builder_config, coverage, step_name,
               divider):
    super().__init__(api, cas_digest, builder_config, coverage, step_name)
    self.divider = divider

  def skip(self):
    return self.api.devtools.is_debug(self.builder_config)

  def construct_commands(self):
    return [cmd + self.extra_args for cmd in self.divider.commands]

  def _process_results(self):
    with self.api.step.nest(self.step_name):
      return self.collect()

  def test_name_to_grep_string(self, name):
    name = re.sub(r'^e2e/.*: ', '', name)
    return super().test_name_to_grep_string(name)

  def trigger_exoneration(self, test_names):
    self.divider = E2ETestDivider(self.api, self.builder_config, shard_count=1)
    return super().trigger_exoneration(test_names)


class RepeatE2EShuffledTests(E2ETests):
  @cached_property
  def exonerator(self):
    return NoOpExonerator(self)

  def skip(self):
    return super().skip() or (not self.api.devtools.is_shuffled_run())

  @property
  def test_type_tag(self):
    return 'shuffled_repeat_e2e_tests'


class PerformanceTests(DevToolsTests):
  @cached_property
  def exonerator(self):
    return NoOpExonerator(self)

  @property
  def test_type_tag(self):
    return 'perf_tests'

  def skip(self):
    return not self.api.properties.get("perf_benchmarks", False)

  def construct_commands(self):
    return [[
          self.api.path.join('third_party', 'node', 'node.py'),
          "--output",
          self.api.path.join('scripts', 'test', 'run_test_suite.js'),
          "--test-suite-path=gen/test/perf",
          "--test-suite-source-dir=test/perf",
          "--test-server-type=hosted-mode",
          "--target=" + self.builder_config,
          '--swarming-output-file',
          '${ISOLATED_OUTDIR}',
      ]]

  def _process_results(self):
    with self.api.step.nest(self.step_name):
      result = self.collect()
      if not result.infra_failures:
        self.copy_perf_benchmarks_data()
      return result

  def copy_perf_benchmarks_data(self):
    shard_output_dir = self.tasks[0].get_task_shard_output_dirs()[0]
    perf_data_dir = (self.output_dir / shard_output_dir / 'perf-data')
    self.api.file.rmtree(
        'remove perf data file if it exists',
        self.api.path.join(self.api.path.checkout_dir, 'perf-data'))
    self.api.file.copytree(
        'copy perf tests data', perf_data_dir,
        self.api.path.join(self.api.path.checkout_dir, 'perf-data'))

class E2ETestDivider:
  def __init__(self, api, builder_config, shard_count=4):
    self.api = api
    self.builder_config = builder_config
    self.shard_count = shard_count

  @cached_property
  def commands(self):
    return self.api.devtools.divided_e2e_commands(
        builder_config=self.builder_config,
        shards=self.shard_count,
    )


def RunSteps(api, builder_config, is_official_build, devtools_skip_typecheck,
             clobber, coverage, perf_benchmarks):
  api.devtools.configure(builder_config, is_official_build,
                         devtools_skip_typecheck)
  api.devtools.update()

  with api.devtools.depot_on_path():
    api.devtools.clean_out_dir(builder_config, clobber)
    api.chromium.run_gn()
    compilation_result = api.chromium.compile()
    if compilation_result.status != common_pb.SUCCESS:
      return compilation_result

    cas_digest = api.devtools.archive_to_cas()
    divider = E2ETestDivider(api, builder_config)
    tests = [
        UnitTests(api, cas_digest, builder_config, coverage,
                          'Unit Tests'),
        InteractionsTests(api, cas_digest, builder_config, coverage,
                          'Interactions Tests'),
        E2ETests(api, cas_digest, builder_config, coverage,
                          'E2E Tests', divider),
        PerformanceTests(api, cas_digest, builder_config, coverage,
                          'Performance Tests'),
        RepeatE2EShuffledTests(api, cas_digest, builder_config, coverage,
                          'Repeat E2E Tests', divider),

    ]
    tests = [t for t in tests if not t.skip()]

    with api.step.nest('Trigger Tests'):
      for t in tests:
        t.trigger()
        t.include_invocations()

    if not api.devtools.is_debug(builder_config):
      with api.step.nest('Linting'):
        run_lint_check(api)

    for t in tests:
      t.process_results()

    with api.step.nest('Flake exonaration attempt'):
      test_names = failed_tests_names(api)

      for t in tests:
        t.trigger_exoneration(test_names)
        t.include_invocations()
      for t in tests:
        t.process_exoneration_results()

    if coverage:
      with api.step.nest('Coverage'):
        publish_coverage_points(api)

    if perf_benchmarks:
      with api.step.nest('Publish performance benchmarks'):
        publish_performance_benchmarks(api)

    results = sum([t.results for t in tests], Results())
    return results.raw_result()

def failed_tests_names(api):
  unexpected_results = api.resultdb.query(
    inv_ids=[api.resultdb.current_invocation.replace('invocations/', '')],
    variants_with_unexpected_results=True,
    tr_fields=['testId', 'tags'],
  )

  def tag_value(result, tag):
    return next(t for t in result.tags if t.key == tag).value

  test_names = defaultdict(set)

  for inv in unexpected_results.values():
    for result in inv.test_results:
      test_type = tag_value(result, 'test_type')
      test_names[test_type].add(result.test_id)

  return test_names

def lint_script_exists(api, name):
  script_file = api.path.checkout_dir.joinpath('scripts', 'test', name)
  return api.path.exists(script_file)

def run_lint_check(api):
  lint_script = 'run_lint_check_js.mjs'
  if not lint_script_exists(api, lint_script):
    lint_script = 'run_lint_check_js.js'
  api.devtools.run_node_script('Lint Check with ESLint', lint_script)
  api.devtools.run_node_script('Lint check with Stylelint',
                               'run_lint_check_css.js')

def publish_performance_benchmarks(api):
  report_file = api.path.checkout_dir.joinpath('perf-data',
                                               'devtools-perf.json')
  front_end_results = api.file.read_json('Read performance data results',
                                         report_file)
  tmp_dir = api.m.path.mkdtemp('perf-results')
  results_file = tmp_dir.join('devtools-perf.json')
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


def publish_coverage_points(api):
  if api.tryserver.is_tryserver:
    return

  api.devtools.run_node_script('Combining coverage reports',
                               'merge_coverage_reports.js')

  dimensions = ["lines", "statements", "functions", "branches"]

  report_file = api.path.checkout_dir.joinpath('karma-coverage',
                                               'coverage-summary.json')

  summary = api.file.read_json(
      'Coverage summary', report_file, test_data=test_cov_data())
  totals = summary['total']
  api.step.active_result.presentation.step_text = "".join([
      "\n%s: %s%%" % (dim.capitalize(), totals[dim]['pct'])
      for dim in dimensions
  ])

  with api.context(cwd=api.path.checkout_dir):
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
          api.path.checkout_dir.joinpath(
              'karma-coverage',
              'coverage-summary.json',
          )),
  )

  yield api.test(
      'official build',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(is_official_build=True, builder_config='Debug'),
      api.post_process(post_process.Filter('gn')),
      status='SUCCESS',
  )

  yield api.test(
      'skip typecheck build',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(devtools_skip_typecheck=True, builder_config='Debug'),
      api.post_process(post_process.Filter('gn')),
      status='SUCCESS',
  )

  yield api.test(
      'skip coverage',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(coverage=False, builder_config='Debug'),
      api.post_process(post_process.Filter('gn')),
      status='SUCCESS',
  )

  yield api.test(
      'perf data',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(perf_benchmarks=True, builder_config='Debug'),
      api.path.exists(
          api.path.checkout_dir.joinpath(
              'perf-data',
              'devtools-perf.json',
          )),
  )

  yield api.test(
      'run performance benchmarks',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(perf_benchmarks=True, builder_config='Debug'),
      api.post_process(post_process.Filter('gn')),
      status='SUCCESS',
  )

  yield api.test(
      'cq parallel builder',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.step_data(
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.post_process(post_process.MustRun, 'archive'),
      api.post_process(post_process.MustRun, 'Trigger Tests.Trigger E2E Tests'),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'ci parallel builder',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_shuffled_linux'),
      api.step_data(
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.post_process(post_process.MustRun, 'archive'),
      api.post_process(post_process.MustRun,
                       'Trigger Tests.Trigger Unit Tests'),
      api.post_process(post_process.MustRun,
                       'Trigger Tests.Trigger Interactions Tests'),
      api.post_process(post_process.MustRun, 'Trigger Tests.Trigger E2E Tests'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      api.post_process(post_process.MustRun, 'Repeat E2E Tests'),
      api.post_process(post_process.Filter().include_re(
          'Trigger Tests.*|.*\(Shard #\d*\).*')),
      status='SUCCESS',
  )
  yield api.test(
      'ci parallel builder performance benchmarks',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.properties(perf_benchmarks=True),
      api.step_data(
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.post_process(post_process.MustRun, 'archive'),
      api.post_process(post_process.MustRun,
                       'Trigger Tests.Trigger Unit Tests'),
      api.post_process(post_process.MustRun,
                       'Trigger Tests.Trigger Interactions Tests'),
      api.post_process(post_process.MustRun, 'Trigger Tests.Trigger E2E Tests'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'Performance Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      api.post_process(post_process.Filter().include_re(
          'Trigger Tests.*|.*\(Shard #\d*\).*')),
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
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node1 runner1 config1 pattern1\nnode2 runner2 config2 pattern2',
              stream='stdout')),
      api.step_data(
          'E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #1) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data2)),
      api.post_process(post_process.MustRun, 'archive'),
      api.post_process(post_process.MustRun, 'Trigger Tests.Trigger E2E Tests'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      resultdb_query(
          'Flake exonaration attempt.rdb query',
          test_result('e2e/file1: etest1', 'e2e_tests'),
          test_result('e2e/file2: etest2', 'e2e_tests'),
      ),
      api.step_data(
          'Flake exonaration attempt.Trigger E2E Tests (rerun).divide test run',
          api.raw_io.stream_output_text(
              'node1 runner1 config1 pattern1',
              stream='stdout')),
      api.step_data(
          'Flake exonaration attempt.E2E Tests (rerun).E2E Tests (rerun) shards'
          ' results.E2E Tests (rerun) (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1),
      ),
      api.post_process(
          post_process.SummaryMarkdown,
          'Failure in E2E Tests (shard #0), Failure in E2E Tests (shard #1), '
          'Failure in E2E Tests (rerun) (shard #0)'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'failed parallel builder on E2E with exonerated tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.step_data(
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node1 runner1 config1 pattern1\nnode2 runner2 config2 pattern2',
              stream='stdout')),
      api.step_data(
          'E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #1) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data2)),
      api.post_process(post_process.MustRun, 'archive'),
      api.post_process(post_process.MustRun, 'Trigger Tests.Trigger E2E Tests'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      resultdb_query(
          'Flake exonaration attempt.rdb query',
          test_result('e2e/file1: e/test/1', 'e2e_tests'),
          test_result('e2e/file2: e/test/2', 'e2e_tests'),
      ),
      api.step_data(
          'Flake exonaration attempt.Trigger E2E Tests (rerun).divide test run',
          api.raw_io.stream_output_text(
              'node1 rerunner1 config1 pattern1',
              stream='stdout')),
      api.post_process(
          post_process.SummaryMarkdown,
          'Flaky tests exonerated: Failure in E2E Tests (shard #0), Failure in'
          ' E2E Tests (shard #1)'),
      api.post_process(post_process.DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'failed parallel builder on E2E with exonerations limit reached',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.step_data(
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node1 runner1 config1 pattern1\nnode2 runner2 config2 pattern2',
              stream='stdout')),
      api.step_data(
          'E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #1) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data2)),
      api.post_process(post_process.MustRun, 'archive'),
      api.post_process(post_process.MustRun, 'Trigger Tests.Trigger E2E Tests'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      resultdb_query(
          'Flake exonaration attempt.rdb query',
          *[
              test_result(f'e2e/file1: e/test/{i}', 'e2e_tests')
              for i in range(FLAKE_DETECTION_MAX_TESTS + 1)
          ],
      ),
      api.post_process(post_process.DoesNotRun, 'Flake exonaration attempt.'
          'Trigger E2E Tests (rerun).divide test run'),
      api.post_process(
          post_process.SummaryMarkdown,
          'Failure in E2E Tests (shard #0), Failure in E2E Tests (shard #1), '
          'Too many failures'),
      api.post_process(post_process.DropExpectation),
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
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.step_data(
          'Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(post_process.SummaryMarkdown,
                       'Infra Failure in Unit Tests (shard #0)'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      api.post_process(post_process.DropExpectation),
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
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.step_data(
          'Performance Tests.Performance Tests ' +
          'shards results.Performance Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(post_process.SummaryMarkdown,
                       'Infra Failure in Performance Tests (shard #0)'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'Performance Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      api.post_process(post_process.DropExpectation),
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
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.step_data(
          'Interactions Tests.Interactions Tests shards ' +
          'results.Interactions Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(post_process.SummaryMarkdown,
                       'Infra Failure in Interactions Tests'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      api.post_process(post_process.DropExpectation),
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
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.step_data(
          'Interactions Tests.Interactions Tests shards ' +
          'results.Interactions Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(post_process.SummaryMarkdown,
                       'Failure in Interactions Tests, Failure in Interactions '
                       'Tests (rerun)'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
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
      api.post_process(post_process.DropExpectation),
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
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.step_data(
          'Interactions Tests.Interactions Tests shards ' +
          'results.Interactions Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(post_process.SummaryMarkdown,
                       'Failure in Interactions Tests (shard #0)'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      api.post_process(post_process.DropExpectation),
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
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.step_data(
          'Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(post_process.SummaryMarkdown,
          'Failure in Unit Tests (shard #0), Failure in Unit Tests (rerun) '
          '(shard #0)'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
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
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
  yield api.test(
      'ci failed parallel builder on unit tests karma file copy',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.step_data(
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.step_data('Unit Tests.copy unit tests coverage data',
                    api.file.errno('WinError 3')),
      api.post_process(post_process.SummaryMarkdown,
                       'Failed to copy coverage data from Unit Tests'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      api.post_process(post_process.DropExpectation),
      status='INFRA_FAILURE',
  )
  yield api.test(
      'ci failed parallel builder on Performance tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.properties(perf_benchmarks=True),
      api.step_data(
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.step_data(
          'Performance Tests.Performance Tests ' +
          'shards results.Performance Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(post_process.SummaryMarkdown,
                       'Failure in Performance Tests (shard #0)'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'Performance Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      api.post_process(post_process.DropExpectation),
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
      api.step_data(
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node1 runner1 config1 pattern1\nnode2 runner2 config2 pattern2',
              stream='stdout')),
      api.step_data(
          'Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'Interactions Tests.Interactions Tests shards ' +
          'results.Interactions Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'Performance Tests.Performance Tests shards ' +
          'results.Performance Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #1) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data2)),
      api.post_process(
          post_process.SummaryMarkdown,
          'Failure in Unit Tests (shard #0), Failure in Interactions Tests '
          '(shard #0), Failure in E2E Tests (shard #0), Failure in E2E Tests '
          '(shard #1), Failure in Performance Tests (shard #0)'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'Performance Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
