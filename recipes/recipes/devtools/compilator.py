# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Compilator recipe.

Triggered by an orchestrating recipe to perform all compilation related tasks.

Additionally we might run checks that do not need to run on swarming
(lint checks, etc).

All properties needed by the orchestrator are returned at the end of the
build:
 - CAS digest.
"""

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb2

from recipe_engine.post_process import DropExpectation, SummaryMarkdown, MustRun, DoesNotRun
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.devtools.e2e_tests_runner import read_test_list

DEPS = [
    'chromium',
    'devtools',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/runtime',
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

CANCELLATION_MESSAGE = (
    'Parent orchestrating build ended, causing this build to be canceled.')


def RunSteps(api, builder_config, is_official_build, devtools_skip_typecheck,
             clobber):
  try:
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

    e2e_tests_list = read_test_list(api, source_dir, builder_config)

    run_lint_check(api, builder_config, source_dir)

    cas_digest = api.devtools.archive_to_cas(source_dir)
    emit_compilator_properties(api, cas_digest, e2e_tests_list)
  finally:
    if api.runtime.in_global_shutdown:
      # pylint: disable=lost-exception
      # Cancellation can cause all sorts of spurious exceptions.
      return result_pb2.RawResult(
          status=common_pb.CANCELED, summary_markdown=CANCELLATION_MESSAGE)


def emit_compilator_properties(api, cas_digest, e2e_tests_list):
  properties = dict()
  properties['cas_digest'] = cas_digest
  properties['e2e_test_list'] = e2e_tests_list

  properties_step = api.step('compilator properties', [])
  properties_step.presentation.properties['compilator_properties'] = properties
  properties_step.presentation.logs['compilator_properties'] = api.json.dumps(
      properties, indent=2)


def run_lint_check(api, builder_config, source_dir):
  if not api.devtools.is_debug(builder_config):
    with api.step.nest('Linting'):
      api.devtools.run_node_script(source_dir, 'Lint Check with ESLint',
                                   'run_lint_check_js.mjs')
      api.devtools.run_node_script(source_dir, 'Lint Check with Stylelint',
                                   'run_lint_check_css.js')


def GenTests(api):

  def check_steps(rule, *step_names):
    return [api.post_process(rule, name) for name in step_names]

  yield api.test('basic')

  yield api.test(
      'compile failure',
      api.step_data('compile', retcode=1),
      *check_steps(MustRun, 'compile'),
      *check_steps(DoesNotRun, 'archive', 'compilator properties'
                   'Linting.Lint Check with ESLint',
                   'Linting.Lint check with Stylelint'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'linter failure',
      api.step_data('Linting.Lint Check with ESLint', retcode=1),
      *check_steps(MustRun, 'compile'),
      *check_steps(DoesNotRun, 'Linting.Lint Check with Stylelint', 'archive',
                   'compilator properties'),
      api.post_process(SummaryMarkdown,
                       "Step('Linting.Lint Check with ESLint') (retcode: 1)"),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'cancellation',
      api.runtime.global_shutdown_on_step('compile'),
      *check_steps(DoesNotRun, 'archive', 'compilator properties'
                   'Linting.Lint Check with ESLint',
                   'Linting.Lint check with Stylelint'),
      api.post_process(SummaryMarkdown, CANCELLATION_MESSAGE),
      api.post_process(DropExpectation),
      status='CANCELED',
  )

  yield api.test(
      'no lining',
      api.properties(builder_config='Debug'),
      *check_steps(
          MustRun,
          'compile',
          'archive',
          'compilator properties',
      ),
      *check_steps(
          DoesNotRun,
          'Linting.Lint Check with ESLint',
          'Linting.Lint check with Stylelint',
      ),
      api.post_process(DropExpectation),
  )
