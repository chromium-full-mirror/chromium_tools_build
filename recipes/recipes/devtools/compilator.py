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
from PB.recipes.build.devtools.compilator import InputProperties

from RECIPE_MODULES.build.chromium_tests.resultdb import ResultDB

DEPS = [
    'chromium',
    'devtools',
    'recipe_engine/buildbucket',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/properties',
    'recipe_engine/resultdb',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'chromium_checkout',
]

PROPERTIES = InputProperties

CANCELLATION_MESSAGE = (
    'Parent orchestrating build ended, causing this build to be canceled.')


def RunSteps(api, properties):
  builder_config = properties.builder_config or 'Release'
  devtools_bundle = properties.devtools_bundle
  if 'devtools_bundle' not in api.properties:
    devtools_bundle = True

  try:
    api.devtools.configure(builder_config, properties.is_official_build,
                           properties.devtools_skip_typecheck,
                           properties.force_host_cpu or None, devtools_bundle)
    update_result = api.devtools.update()
    update_result.out_commit.position = 1
    api.chromium_checkout.update_rdb_source_spec_invocation(
        gitiles_commit=update_result.out_commit)
    build_dir = api.devtools.source_dir / 'out' / api.chromium.c.build_config_fs
    with api.devtools.depot_on_path():
      api.devtools.clean_out_dir(builder_config, True)
      with api.chromium.guard_compile(build_dir):
        api.chromium.run_gn(api.devtools.source_dir, build_dir)
        targets = None if devtools_bundle else ['assert_grd']
        compilation_result = api.chromium.compile(
            api.devtools.source_dir, build_dir, targets=targets)
        if compilation_result.status != common_pb.SUCCESS:
          return compilation_result

    emit_compilator_properties(
        api, {
            'cas_digest':
                api.devtools.archive_to_cas(),
        })
  finally:
    if api.runtime.in_global_shutdown:
      # pylint: disable=lost-exception
      # Cancellation can cause all sorts of spurious exceptions.
      return result_pb2.RawResult(
          status=common_pb.CANCELED, summary_markdown=CANCELLATION_MESSAGE)


def emit_compilator_properties(api, properties):
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
