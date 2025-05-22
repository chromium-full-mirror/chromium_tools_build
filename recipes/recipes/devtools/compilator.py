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
from PB.go.chromium.org.luci.resultdb.proto.v1 import common as common_rdb_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import invocation as invocation_pb
from PB.recipe_engine import result as result_pb2

from recipe_engine.post_process import DropExpectation, SummaryMarkdown, MustRun, DoesNotRun
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.build.chromium_tests.resultdb import ResultDB
from RECIPE_MODULES.build.devtools.e2e_tests_runner import read_test_list

DEPS = [
    'chromium', 'devtools', 'recipe_engine/buildbucket', 'recipe_engine/file',
    'recipe_engine/json', 'recipe_engine/properties', 'recipe_engine/resultdb',
    'recipe_engine/runtime', 'recipe_engine/step', 'chromium_checkout'
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
    'target_cpu':
        Property(
            kind=str,
            help='Target cpu architecture for compilation',
            default=None),
}

CANCELLATION_MESSAGE = (
    'Parent orchestrating build ended, causing this build to be canceled.')


def RunSteps(api, builder_config, is_official_build, devtools_skip_typecheck,
             clobber, target_cpu):
  try:
    api.devtools.configure(builder_config, is_official_build,
                           devtools_skip_typecheck, target_cpu)
    update_result = api.devtools.update()
    update_result.out_commit.position = 1
    api.chromium_checkout.update_rdb_source_spec_invocation(
        gitiles_commit=update_result.out_commit)
    build_dir = api.chromium.default_build_dir(api.devtools.source_dir)
    with api.devtools.depot_on_path():
      api.devtools.clean_out_dir(builder_config, clobber)
      api.chromium.run_gn(api.devtools.source_dir, build_dir)

      compilation_result = api.chromium.compile(api.devtools.source_dir,
                                                build_dir)
      if compilation_result.status != common_pb.SUCCESS:
        return compilation_result

    e2e_tests_list = read_test_list(api, builder_config)

    cas_digest = api.devtools.archive_to_cas()
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

def GenTests(api):

  def check_steps(rule, *step_names):
    return [api.post_process(rule, name) for name in step_names]

  yield api.test('basic')

  yield api.test(
      'compile failure',
      api.step_data('compile', retcode=1),
      *check_steps(MustRun, 'compile'),
      *check_steps(DoesNotRun, 'archive', 'compilator properties'),
      api.post_process(DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'cancellation',
      api.runtime.global_shutdown_on_step('compile'),
      *check_steps(DoesNotRun, 'archive', 'compilator properties'),
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
      api.post_process(DropExpectation),
  )
