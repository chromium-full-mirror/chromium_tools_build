# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from recipe_engine import post_process
from recipe_engine.recipe_api import Property

import json

DEPS = [
    'builder_group',
    'chromium',
    'devtools',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/git',
    'depot_tools/tryserver',
    'perf_dashboard',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
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
}


def RunSteps(api, builder_config, is_official_build, devtools_skip_typecheck,
             clobber):
  api.devtools.configure(builder_config, is_official_build,
                         devtools_skip_typecheck)
  api.devtools.update()

  with api.devtools.depot_on_path():
    api.devtools.clean_out_dir(builder_config, clobber)
    api.chromium.run_gn()
    compilation_result = api.chromium.compile()
    if compilation_result.status != common_pb.SUCCESS:
      return compilation_result

    run_unit_tests(api, builder_config)
    run_interactions(api, builder_config)
    publish_coverage_points(api)

    if api.devtools.is_debug(builder_config):
      return

    run_lint_check(api)

    if api.devtools.is_parallel_run():
      api.devtools.run_e2e(builder_config, run_mode='parallel')
      api.devtools.run_e2e(builder_config, run_mode='sequential')
    else:
      api.devtools.run_e2e(builder_config)

    if can_run_experimental_steps(api):
      # Place here any unstable steps that you want to be performed on
      # builders with property run_experimental_steps == True
      pass


def run_script(api, step_name, script, args=None):
  with api.step.defer_results():
    sc_path = api.path['checkout'].join('scripts', 'test', script)
    args = ["vpython3", "-u", sc_path] + (args or [])
    api.step(step_name, args)


def run_unit_tests(api, builder_config):
  run_script(api, 'Unit Tests', 'run_unittests.py', [
      '--target=' +  builder_config,
      '--coverage',
    ])

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


def run_interactions(api, builder_config):
  bucket = 'devtools-frontend-screenshots'
  with api.devtools.collect_screenshots_on_trybot(bucket):
    api.devtools.rdb_node_script(
        'Interactions',
        'run_test_suite.js',
        [
            "--test-suite-path=gen/test/interactions",
            "--test-suite-source-dir=test/interactions",
            "--test-server-type='component-docs'",
            "--target=" + builder_config,
            "--coverage"
        ],
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
      try_build(builder='linux'),
  )

  yield api.test(
      'experimental',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='linux'),
      api.properties(run_experimental_steps=True),
  )

  yield api.test(
      'compile failure',
      api.builder_group.for_current('devtools-frontend'),
      ci_build(builder='linux'),
      api.step_data('compile', retcode=1),
      api.post_process(post_process.StatusFailure),
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
      api.post_process(post_process.Filter('gn'))
  )

  yield api.test(
      'skip typecheck build',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(devtools_skip_typecheck=True),
      api.post_process(post_process.Filter('gn'))
  )

  yield api.test(
      'new lint check',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      ci_build(builder='linux'),
      api.properties(builder_config='Debug'),
      api.path.exists(api.path['checkout'].join(
          'scripts', 'test', 'run_lint_check_js.mjs'
      )),
  )

  yield api.test(
      'parallel builder',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='parallel_linux'),
      api.post_process(post_process.MustRun, 'E2E tests (Parallel)'),
      api.post_process(post_process.MustRun, 'E2E tests (Sequential)'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation))
