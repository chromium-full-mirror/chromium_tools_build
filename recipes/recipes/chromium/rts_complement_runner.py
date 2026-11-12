# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Runs the RTS complement (inverse) tests of one CQ build.

Launched by chromium/rts_complement_trigger, one build per CQ build. It works
like chromium/orchestrator without the compile: instead of triggering a
compilator build, it reads the CQ build (cq_build_id), resolves its with-patch
SUCCESS compilator child build, and reads that compilator build's outputs.

Only the suites RTS skipped tests of in the CQ build are selected, from its
RTS evaluation output properties, with the 'rts_complement' command lines,
which run the skipped tests. Running the tests and recording false acceptances
are stubs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from google.protobuf import json_format

from recipe_engine import post_process
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipe_modules.build.chromium_orchestrator import (
  properties as orchestrator_pb,
)
from PB.recipes.build.chromium.rts_complement_runner import InputProperties
from RECIPE_MODULES.build import (
  chromium,
  chromium_orchestrator,
  chromium_rts,
  chromium_tests,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.build.chromium_rts.api import RTS_COMPLEMENT_VARIANT
from RECIPE_MODULES.build.chromium_rts.evaluation import SuiteSafetyDetails
from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import buildbucket, file, properties, step

PROPERTIES = InputProperties


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_orchestrator: chromium_orchestrator.API
  chromium_rts: chromium_rts.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_orchestrator: chromium_orchestrator.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  file: file.TEST_API
  properties: properties.TEST_API


def _select_complement_suites(
  api: DEPS,
  suites: Sequence[steps.Test],
  suite_safety_details: Mapping[str, SuiteSafetyDetails],
) -> list[steps.Test]:
  """Returns the suites to run the skipped tests of.

  Only selects the suites the CQ build ran with RTS command lines
  (actively_filtered in its rts_suite_safety_details). A suite without a
  'rts_complement' command line would run in full, so it's skipped.
  """
  skipped_counts = {
    name: int(details.get('rts_skipped_tests_count', 0))
    for name, details in suite_safety_details.items()
    if details.get('actively_filtered')
  }
  selected = []
  lines = []
  skip_reasons = dict.fromkeys(
    skipped_counts.keys() - {s.name for s in suites},
    'not in the compilator tests',
  )
  for suite in suites:
    if suite.name not in skipped_counts:
      # evaluate_rts leaves out the suites that didn't complete in CQ.
      if api.chromium_rts.is_overwritten(suite):
        skip_reasons[suite.name] = 'not actively filtered in CQ'
      continue
    skipped = skipped_counts[suite.name]
    # Only swarming suites get RTS command lines.
    if not api.chromium_rts.is_overwritten(suite):
      skip_reasons[suite.name] = 'no rts_complement command line'
    elif not skipped:
      skip_reasons[suite.name] = 'no tests skipped by RTS'
    else:
      selected.append(suite)
      lines.append(f'{suite.name}: {skipped} skipped tests')
  lines.extend(
    f'{name}: skipped, {reason}'
    for name, reason in sorted(skip_reasons.items())
  )
  api.step.empty(
    'select complement suites',
    step_text=(
      '\n'.join(['', *lines]) if lines else 'no suites filtered by RTS in CQ'
    ),
  )
  return selected


def _run_complement_suites(
  api: DEPS,
  suites: Sequence[steps.Test],
) -> list[steps.Test]:
  """Runs the complement tests and returns the failing suites (stub).

  TODO(crbug.com/565109194): set the ResultDB base variant builder to the CQ
  builder, lower chromium_swarming.default_priority below CQ, tag tasks with
  rts_run_type:complement, and run `suites` with
  test_utils.run_tests_with_patch() inside chromium_tests.wrap_chromium_tests()
  (retrying failed shards and exonerating known flaky failures with LUCI
  Analysis, without a without patch retry).
  """
  api.step.empty(
    'run complement tests (stub)',
    step_text=f'Would run {len(suites)} complement test suites',
  )
  return []


def _record_false_acceptances(
  api: DEPS,
  cq_build: build_pb.Build,
  failing_suites: Sequence[steps.Test],
) -> None:
  """Records the skipped tests that fail, as data (stub).

  TODO(crbug.com/565109194): write the rts_complement output property with the
  per-suite test counts and false acceptances instead of failing this build.
  """
  api.step.empty(
    'record false acceptances (stub)',
    step_text=(
      f'{len(failing_suites)} failing suites for CQ build {cq_build.id}'
    ),
  )


def RunSteps(api: DEPS, props: InputProperties) -> result_pb.RawResult:
  # The CQ build's CL makes the results attributed to it in ResultDB.
  api.tryserver.require_is_tryserver()

  with api.chromium.chromium_layout():
    cq_build = api.buildbucket.get(
      props.cq_build_id,
      step_name='read CQ build',
      fields={'id', 'builder', 'output.properties'},
    )
    # Written by chromium_rts.evaluate_rts, which only runs under the
    # chromium_rts.filter_file_analysis experiment.
    cq_output = json_format.MessageToDict(cq_build.output.properties)
    rts_status = cq_output.get('rts_evaluation_status')
    # rts_complement_trigger treats SUCCESS runners as covered. FAILURE, not
    # INFRA_FAILURE, since a retry reads the same CQ build.
    # TODO(crbug.com/535256440): skip these CQ builds in the trigger.
    if rts_status != 'SUCCESS':
      return result_pb.RawResult(
        status=common_pb.FAILURE,
        summary_markdown=(
          f'CQ build {cq_build.id} has no RTS evaluation results '
          f'(rts_evaluation_status: {rts_status})'
        ),
      )

    # The compilator builder name comes from the CQ builder's properties.
    compilator_build = api.buildbucket.get(
      api.chromium_orchestrator.find_compilator_build_id(cq_build),
      step_name='read compilator build',
      fields={
        'id',
        'builder',
        'status',
        'summary_markdown',
        'output',
        'infra.resultdb',
      },
    )

    builder_id, builder_config = api.chromium_orchestrator.configure_build()

    compilator_output, raw_result = api.chromium_orchestrator.process_sub_build(
      compilator_build, is_compile_phase=True, with_patch=True
    )
    # The compilator had nothing to trigger or failed.
    if raw_result:
      return raw_result

    # Selects the command lines running the tests RTS skipped. Don't call
    # chromium_rts.evaluate_rts, these tests aren't the selected ones.
    _, suites = api.chromium_orchestrator.prepare_tests_from_compilator_output(
      builder_id,
      builder_config,
      compilator_output,
      command_line_variant=RTS_COMPLEMENT_VARIANT,
    )
    suites = _select_complement_suites(
      api, suites, cq_output.get('rts_suite_safety_details', {})
    )
    failing_suites = _run_complement_suites(api, suites)
    _record_false_acceptances(api, cq_build, failing_suites)

  return result_pb.RawResult(
    status=common_pb.SUCCESS,
    summary_markdown=(
      f'CQ build {cq_build.id} ({cq_build.builder.builder}), compilator build '
      f'{compilator_build.id}'
    ),
  )


def GenTests(api: TEST_DEPS):
  ctbc_api = api.chromium_tests_builder_config

  def runner_build():
    return (
      api.chromium.try_build(
        builder_group='fake-try-group',
        builder='rts-complement-runner',
      )
      + ctbc_api.properties(
        ctbc_api.properties_assembler_for_try_builder()
        .with_mirrored_builder(
          builder_group='fake-group',
          builder='fake-builder',
        )
        .assemble()
      )
      + api.properties(InputProperties(cq_build_id=1000))
      + api.properties(
        **{
          '$build/chromium_orchestrator': orchestrator_pb.InputProperties(
            compilator='win-rel-compilator'
          )
        }
      )
    )

  def actively_filtered(skipped):
    return {'actively_filtered': True, 'rts_skipped_tests_count': skipped}

  default_cq_output = {
    'rts_evaluation_status': 'SUCCESS',
    'rts_suite_safety_details': {
      'browser_tests': actively_filtered(350),
      'content_unittests': actively_filtered(20),
      'interactive_ui_tests': actively_filtered(0),
      # Listed for its recall, but RTS didn't filter it.
      'base_unittests': {'rts_skipped_tests_count': 5, 'test_recall_rate': 0.5},
      'sync_integration_tests': actively_filtered(7),
    },
  }

  def cq_build(output=None):
    build = build_pb.Build(
      id=1000,
      builder=dict(project='chromium', bucket='try', builder='win-rel'),
    )
    build.output.properties.update(
      default_cq_output if output is None else output
    )
    return api.buildbucket.simulated_get(build, step_name='read CQ build')

  all_tests = [
    'browser_tests',
    'content_unittests',
    'interactive_ui_tests',
    'base_unittests',
  ]

  def targets_spec():
    def gtest(name, shards, enable_rts_filtering=True):
      return {
        'name': name,
        'swarming': {'shards': shards},
        'enable_rts_filtering': enable_rts_filtering,
      }

    return api.chromium_tests.read_targets_spec(
      'fake-group',
      {
        'fake-builder': {
          'gtest_tests': [
            gtest('browser_tests', 10),
            gtest('content_unittests', 2),
            gtest('interactive_ui_tests', 3),
            gtest('base_unittests', 1, enable_rts_filtering=False),
          ],
        },
      },
    )

  def command_lines():
    base = {t: [f'./{t}'] for t in all_tests}
    rts = {t: [f'./{t}', f'--filter=gen/rts/{t}.filter'] for t in all_tests}
    # content_unittests has no inverted filter file.
    complement = {
      t: [f'./{t}', f'--filter=gen/rts/{t}_inverted.filter']
      for t in all_tests
      if t != 'content_unittests'
    }
    return (
      api.step_data('read command lines', api.file.read_json(base))
      + api.step_data('read rts command lines', api.file.read_json(rts))
      + api.step_data(
        'read rts_complement command lines', api.file.read_json(complement)
      )
    )

  def compilator_build(include_swarming_props=True):
    output_properties = api.chromium_orchestrator.get_compilator_output_props(
      tests=all_tests,
      include_swarming_props=include_swarming_props,
      include_rts_props=True,
    )
    if include_swarming_props:
      output_properties['swarming_trigger_properties'][
        'swarming_command_lines_variant_digests'
      ]['rts_complement'] = 'complement-digest/1'
    build = build_pb.Build(
      id=2000,
      status=common_pb.SUCCESS,
      summary_markdown='compilator summary',
      output=dict(
        gitiles_commit=dict(
          host='chromium.googlesource.com',
          project='chromium/src',
          id='cd7164f91fe44b4ec2304df88aa01da1ec930dd2',
          ref='refs/heads/main',
          position=1069217,
        )
      ),
    )
    build.output.properties.update(output_properties)
    return api.buildbucket.simulated_search_results(
      [build_pb.Build(id=2000, status=common_pb.SUCCESS)],
      step_name='find compilator build',
    ) + api.buildbucket.simulated_get(build, step_name='read compilator build')

  def full_build(cq_output=None):
    return (
      runner_build()
      + cq_build(cq_output)
      + compilator_build()
      + targets_spec()
      + command_lines()
    )

  yield api.test(
    'basic',
    full_build(),
    api.post_check(post_process.MustRun, 'set rdb sources'),
    api.post_check(
      post_process.StepTextEquals,
      'select complement suites',
      '<br/>browser_tests: 350 skipped tests'
      '<br/>content_unittests: skipped, no rts_complement command line'
      '<br/>interactive_ui_tests: skipped, no tests skipped by RTS'
      '<br/>sync_integration_tests: skipped, not in the compilator tests',
    ),
    api.post_check(
      post_process.StepTextEquals,
      'run complement tests (stub)',
      'Would run 1 complement test suites',
    ),
    api.post_check(post_process.MustRun, 'record false acceptances (stub)'),
    api.post_check(
      post_process.SummaryMarkdown,
      'CQ build 1000 (win-rel), compilator build 2000',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_actively_filtered_suites',
    # No rts_suite_safety_details, e.g. the filtered suites didn't complete.
    full_build({'rts_evaluation_status': 'SUCCESS'}),
    api.post_check(
      post_process.StepTextEquals,
      'select complement suites',
      '<br/>browser_tests: skipped, not actively filtered in CQ'
      '<br/>interactive_ui_tests: skipped, not actively filtered in CQ',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'rts_evaluation_error',
    runner_build(),
    cq_build({'rts_evaluation_status': 'ERROR'}),
    api.post_check(
      post_process.SummaryMarkdown,
      'CQ build 1000 has no RTS evaluation results '
      '(rts_evaluation_status: ERROR)',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_rts_evaluation',
    runner_build(),
    # E.g. no RTS evaluation ran in the CQ build.
    cq_build({}),
    api.post_check(post_process.DoesNotRun, 'find compilator build'),
    api.post_check(
      post_process.SummaryMarkdown,
      'CQ build 1000 has no RTS evaluation results '
      '(rts_evaluation_status: None)',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_tests_to_trigger',
    runner_build(),
    cq_build(),
    compilator_build(include_swarming_props=False),
    api.post_check(post_process.DoesNotRun, 'select complement suites'),
    api.post_check(post_process.SummaryMarkdown, 'compilator summary'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'missing_compilator',
    runner_build(),
    cq_build(),
    api.buildbucket.simulated_search_results(
      [], step_name='find compilator build'
    ),
    api.post_check(post_process.DoesNotRun, 'read compilator build'),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'not_a_tryjob',
    api.chromium.ci_build(builder_group='fake-group', builder='fake-builder'),
    api.post_check(post_process.MustRun, 'not a tryjob'),
    api.post_check(post_process.DoesNotRun, 'read CQ build'),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
