# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from abc import ABC, abstractmethod
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from recipe_engine import post_process
from recipe_engine.recipe_api import Property
from recipe_engine.recipe_api import InfraFailure, StepFailure

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
    'parallel':
        Property(
            kind=bool,
            help='Switch swarming assisted parallel executions of tests',
            default=False),
}


class Results():

  def __init__(self, infra_failures=None, test_failures=None):
    self.infra_failures = infra_failures or []
    self.test_failures = test_failures or []

  def __add__(self, result):
    return Results(
        self.infra_failures + result.infra_failures,
        self.test_failures + result.test_failures,
    )

  def add_infra_failure(self, failure):
    self.infra_failures.append(failure)

  def add_test_failure(self, failure):
    self.test_failures.append(failure)

  def raise_on_failure(self):
    """
    Prioritize test failures in order to be able to close the tree even if we
    have infra failures.
    """
    if self.test_failures:
      raise StepFailure(', '.join(self.test_failures))

    if self.infra_failures:
      raise InfraFailure(', '.join(self.infra_failures))


class DevToolsTests(ABC):

  def __init__(self, api, cas_digest, builder_config, coverage, step_name):
    self.api = api
    self.cas_digest = cas_digest
    self.builder_config = builder_config
    self.step_name = step_name
    self.output_dir = self.api.path.mkdtemp()
    self.tasks = []
    self.coverage = coverage

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

  @abstractmethod
  def trigger(self):
    """
    Triggers the command(s) we want to run on Swarming tasks.
    """

  @abstractmethod
  def process_results(self):
    """
    Collects the tasks with '_collect_tasks' and does any extra things we want
    to do after the collection. Returns a list of the failures as strings
    (empty list if there are no failures).
    """


class UnitTests(DevToolsTests):

  def trigger(self):
    shuffle = ['--shuffle'] if self.api.devtools.is_shuffled_run() else []
    with self.api.step.nest(f'Trigger {self.step_name}'):
      command = [
          self.api.path.join('scripts', 'test', 'run_unittests.py'),
          '--target=' + self.builder_config,
          '--swarming-output-file',
          '${ISOLATED_OUTDIR}',
      ]
      if self.coverage:
        command.append('--coverage')
      self.tasks = self.api.devtools.trigger_test_swarming_tasks(
          step_name=self.step_name,
          cas_digest=self.cas_digest,
          task_output_dir=self.output_dir,
          rdb_wrapped=True,
          commands=[command + shuffle],
      )

  def process_results(self):
    with self.api.step.nest(self.step_name):
      result = self.collect()
      if not result.infra_failures:
        if self.coverage:
          self.copy_coverage_data()
      return result

  def copy_coverage_data(self):
    shard_output_dir = self.tasks[0].get_task_shard_output_dirs()[0]
    coverage_data_dir = self.output_dir / shard_output_dir / 'karma-coverage'
    self.api.file.rmtree(
        'remove coverage files if they exist',
        self.api.path.join(self.api.path['checkout'], 'karma-coverage'))
    self.api.file.copytree(
        'copy unit tests coverage data', coverage_data_dir,
        self.api.path.join(self.api.path['checkout'], 'karma-coverage'))


class InteractionsTests(DevToolsTests):

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
        return Results(test_failures=[f'Failure in {self.step_name}'])
      return Results()

    return super().collect()

  def trigger(self):
    with self.api.step.nest(f'Trigger {self.step_name}'):
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
      ]
      if self.coverage:
        command.append('--coverage')
      self.tasks = self.api.devtools.trigger_test_swarming_tasks(
          step_name=self.step_name,
          cas_digest=self.cas_digest,
          task_output_dir=self.output_dir,
          rdb_wrapped=True,
          env={
              "FORCE_UPDATE_ALL_GOLDENS":
                  'True',
              "THROW_AFTER_GOLDENS_UPDATE":
                  'True',
              "HTML_OUTPUT_FILE":
                  self.api.path.join('${ISOLATED_OUTDIR}',
                                     'interactions_failure_screenshots.html'),
          },
          commands=[command],
      )

  def process_results(self):
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
        self.api.path.join(self.api.path['checkout'], 'interactions-coverage'))
    self.api.file.copytree(
        'copy interaction tests coverage data', coverage_data_dir,
        self.api.path.join(self.api.path['checkout'], 'interactions-coverage'))

  def copy_golden_snapshots(self):
    shard_output_dir = self.tasks[0].get_task_shard_output_dirs()[0]
    golden_snapshots_dir = self.output_dir / shard_output_dir / 'goldens'
    self.api.file.rmtree(
        'remove previous goldens',
        self.api.path.join(self.api.path['checkout'], 'test', 'interactions',
                           'goldens'))
    self.api.file.copytree(
        'copy golden snapshots', golden_snapshots_dir,
        self.api.path.join(self.api.path['checkout'], 'test', 'interactions',
                           'goldens'))


class E2ETests(DevToolsTests):

  def trigger(self):
    with self.api.step.nest(f'Trigger {self.step_name}'):
      commands = self.api.devtools.divided_e2e_commands(
          builder_config=self.builder_config,
          shuffle=self.api.devtools.is_shuffled_run(),
      )
      self.tasks = self.api.devtools.trigger_test_swarming_tasks(
          step_name=self.step_name,
          cas_digest=self.cas_digest,
          commands=commands,
          rdb_wrapped=True,
          env={
              "HTML_OUTPUT_FILE":
                  self.api.path.join('${ISOLATED_OUTDIR}',
                                     'e2e_failure_screenshots.html'),
          },
      )

  def process_results(self):
    with self.api.step.nest(self.step_name):
      return self.collect()


def RunSteps(api, builder_config, is_official_build, devtools_skip_typecheck,
             clobber, coverage, parallel):
  api.devtools.configure(builder_config, is_official_build,
                         devtools_skip_typecheck)
  api.devtools.update()

  with api.devtools.depot_on_path():
    api.devtools.clean_out_dir(builder_config, clobber)
    api.chromium.run_gn()
    compilation_result = api.chromium.compile()
    if compilation_result.status != common_pb.SUCCESS:
      return compilation_result

    if not parallel:
      run_unit_tests(api, builder_config, coverage)
      run_interactions(api, builder_config, coverage)

      if coverage:
        publish_coverage_points(api)

      if api.devtools.is_debug(builder_config):
        return

      run_lint_check(api)

      api.devtools.run_e2e(builder_config)
    else:
      cas_digest = api.devtools.archive_to_cas()
      tests = [
          UnitTests(api, cas_digest, builder_config, coverage, 'Unit Tests'),
          InteractionsTests(api, cas_digest, builder_config, coverage,
                            'Interactions Tests'),
      ]

      if not api.devtools.is_debug(builder_config):
        tests += [
            E2ETests(api, cas_digest, builder_config, coverage, 'E2E Tests')
        ]

      with api.step.nest('Trigger Tests'):
        for t in tests:
          t.trigger()
          t.include_invocations()

      if not api.devtools.is_debug(builder_config):
        with api.step.nest('Linting'):
          run_lint_check(api)

      all_results = sum((t.process_results() for t in tests), Results())

      if coverage:
        with api.step.nest('Coverage'):
          publish_coverage_points(api)

      all_results.raise_on_failure()

    if can_run_experimental_steps(api):
      # Place here any unstable steps that you want to be performed on
      # builders with property run_experimental_steps == True
      pass


def run_script(api, step_name, script, args=None):
  sc_path = api.path['checkout'].join('scripts', 'test', script)
  args = ["vpython3", "-u", sc_path] + (args or [])
  api.step(step_name, args)


def run_unit_tests(api, builder_config, coverage):
  args = [
      '--target=' + builder_config,
  ]
  if coverage:
    args.append('--coverage')
  run_script(api, 'Unit Tests', 'run_unittests.py', args)


def lint_script_exists(api, name):
  script_file = api.path['checkout'].join('scripts', 'test', name)
  return api.path.exists(script_file)

def run_lint_check(api):
  lint_script = 'run_lint_check_js.mjs'
  if not lint_script_exists(api, lint_script):
    lint_script = 'run_lint_check_js.js'
  api.devtools.run_node_script('Lint Check with ESLint', lint_script)
  api.devtools.run_node_script('Lint check with Stylelint',
                               'run_lint_check_css.js')


def run_interactions(api, builder_config, coverage):
  bucket = 'devtools-frontend-screenshots'
  with api.devtools.collect_screenshots_on_trybot(bucket):
    args = [
        "--test-suite-path=gen/test/interactions",
        "--test-suite-source-dir=test/interactions",
        "--test-server-type='component-docs'", "--target=" + builder_config
    ]
    if coverage:
      args.append('--coverage')
    api.devtools.rdb_node_script(
        'Interactions',
        'run_test_suite.js',
        args,
    )


def can_run_experimental_steps(api):
  return api.properties.get('run_experimental_steps', False)


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

  report_file = api.path['checkout'].join('karma-coverage',
                                          'coverage-summary.json')
  if not api.path.exists(report_file):
    return

  summary = api.file.read_json(
      'Coverage summary', report_file, test_data=test_cov_data())
  totals = summary['total']
  api.step.active_result.presentation.step_text = "".join([
      "\n%s: %s%%" % (dim.capitalize(), totals[dim]['pct'])
      for dim in dimensions
  ])

  with api.context(cwd=api.path['checkout']):
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
      'basic try',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      api.post_process(post_process.DoesNotRun, 'archive'),
      try_build(builder='linux'),
      status='SUCCESS',
  )

  yield api.test(
      'experimental',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='linux'),
      api.properties(run_experimental_steps=True),
      status='SUCCESS',
  )

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
      api.path.exists(api.path['checkout'].join(
          'karma-coverage',
          'coverage-summary.json',
      )),
  )

  yield api.test(
      'official build',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(is_official_build=True),
      api.post_process(post_process.Filter('gn')),
      status='SUCCESS',
  )

  yield api.test(
      'skip typecheck build',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(devtools_skip_typecheck=True),
      api.post_process(post_process.Filter('gn')),
      status='SUCCESS',
  )

  yield api.test(
      'skip coverage',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(coverage=False),
      api.post_process(post_process.Filter('gn')),
      status='SUCCESS',
  )

  yield api.test(
      'new lint check',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(builder_config='Debug'),
      api.path.exists(api.path['checkout'].join(
          'scripts', 'test', 'run_lint_check_js.mjs'
      )),
      status='SUCCESS',
  )

  yield api.test(
      'cq parallel builder',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.properties(parallel=True),
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
      ci_build(builder='parallel_linux'),
      api.properties(parallel=True),
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
  yield api.test(
      'failed parallel builder on E2E',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.properties(parallel=True),
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
      api.post_process(
          post_process.SummaryMarkdown,
          'Failure in E2E Tests (shard #0), Failure in' +
          ' E2E Tests (shard #1)'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
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
      api.properties(parallel=True),
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
      'cq infra failure for parallel builder on interactions',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.properties(parallel=True),
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
      api.properties(parallel=True),
      api.step_data(
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.step_data(
          'Interactions Tests.Interactions Tests shards ' +
          'results.Interactions Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(post_process.SummaryMarkdown,
                       'Failure in Interactions Tests'),
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
      'ci failed parallel builder on interactions',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.properties(parallel=True),
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
      api.properties(parallel=True),
      api.step_data(
          'Trigger Tests.Trigger E2E Tests.divide test run',
          api.raw_io.stream_output_text(
              'node runner config pattern', stream='stdout')),
      api.step_data(
          'Unit Tests.Unit Tests ' +
          'shards results.Unit Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data)),
      api.post_process(post_process.SummaryMarkdown,
                       'Failure in Unit Tests (shard #0)'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
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
      'ci failed parallel builder on unit, interactions, and E2E tests',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='parallel_linux'),
      api.properties(parallel=True),
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
          'E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #0) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data1)),
      api.step_data(
          'E2E Tests.E2E Tests shards results.' +
          'E2E Tests (Shard #1) on Ubuntu-22.04',
          api.chromium_swarming.summary(None, data2)),
      api.post_process(
          post_process.SummaryMarkdown,
          'Failure in Unit Tests (shard #0), Failure in Interactions' +
          ' Tests (shard #0), Failure in E2E Tests (shard #0),' +
          ' Failure in E2E Tests (shard #1)'),
      api.post_process(post_process.MustRun, 'Unit Tests'),
      api.post_process(post_process.MustRun, 'Interactions Tests'),
      api.post_process(post_process.MustRun, 'E2E Tests'),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
