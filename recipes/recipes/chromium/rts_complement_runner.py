# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Runs the RTS complement (inverse) tests of one CQ build.

Launched by chromium/rts_complement_trigger, one build per CQ build. It works
like chromium/orchestrator without the compile: instead of triggering a
compilator build, it reads the CQ build (cq_build_id), resolves its with-patch
SUCCESS compilator child build, and reads that compilator build's outputs.

Only the suites RTS skipped tests of in the CQ build run, selected from its
RTS evaluation output properties, with the 'rts_complement' command lines,
which run the skipped tests. Skipped tests that fail are false acceptances of
RTS: they fail this build (FAILURE) and their suites are published in the
failed_suites output property.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import attr
from google.protobuf import json_format

from recipe_engine import post_process
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import common as rdb_common_pb
from PB.go.chromium.org.luci.resultdb.proto.v1 import (
  test_result as test_result_pb,
)
from PB.recipe_engine import result as result_pb
from PB.recipe_modules.build.chromium_orchestrator import (
  properties as orchestrator_pb,
)
from PB.recipes.build.chromium.rts_complement_runner import (
  InputProperties,
  OutputProperties,
)
from RECIPE_MODULES.build import (
  chromium,
  chromium_checkout,
  chromium_orchestrator,
  chromium_rts,
  chromium_swarming,
  chromium_tests,
  chromium_tests_builder_config,
  code_coverage,
  test_utils,
)
from RECIPE_MODULES.build.chromium_rts.api import RTS_COMPLEMENT_VARIANT
from RECIPE_MODULES.build.chromium_rts.evaluation import SuiteSafetyDetails
from RECIPE_MODULES.build.chromium_tests import steps
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  file,
  json,
  luci_analysis,
  properties,
  raw_io,
  resultdb,
  step,
  swarming,
)

PROPERTIES = InputProperties

# Lower than the CQ priority (30), so the complement tests don't delay CQ
# tests. Retried shards run at COMPLEMENT_PRIORITY - 1 (69), because
# SwarmingTest.SUFFIXES_TO_INCREASE_PRIORITY bumps 'retry shards with patch';
# that's still far below CQ, keep it.
COMPLEMENT_PRIORITY = 70
COMPLEMENT_TAG = 'rts_run_type:complement'
# Complement tasks queue behind CQ tasks at COMPLEMENT_PRIORITY, so the 1h
# default expires them at peak. Suites that set their own swarming expiration
# keep it. If both the tasks and their shard retry queue for close to 2h, the
# runner hits its 4h execution timeout and ends in INFRA_FAILURE.
COMPLEMENT_EXPIRATION_SECS = 2 * 60 * 60


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_orchestrator: chromium_orchestrator.API
  chromium_rts: chromium_rts.API
  chromium_swarming: chromium_swarming.API
  chromium_tests: chromium_tests.API
  code_coverage: code_coverage.API
  step: step.API
  test_utils: test_utils.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_orchestrator: chromium_orchestrator.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  code_coverage: code_coverage.TEST_API
  file: file.TEST_API
  json: json.TEST_API
  luci_analysis: luci_analysis.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  resultdb: resultdb.TEST_API
  swarming: swarming.TEST_API


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
  builder_config: chromium_tests_builder_config.BuilderConfig,
  suites: Sequence[steps.Test],
) -> tuple[list[str], list[str]]:
  """Runs the complement tests on Swarming.

  Mirrors the test phase of chromium_orchestrator's test_patch(): failed and
  invalid shards are retried if the CQ builder has retry_failed_shards,
  without a without patch retry and without processing coverage.

  Returns the names of the suites without valid results (e.g. expired shards)
  and of the suites with valid results and failing tests.
  """
  # configure_swarming() in prepare_tests_from_compilator_output() sets the
  # CQ priority, so this overrides it.
  api.chromium_swarming.default_priority = COMPLEMENT_PRIORITY
  api.chromium_swarming.default_expiration = COMPLEMENT_EXPIRATION_SECS
  api.chromium_swarming.add_default_tag(COMPLEMENT_TAG)
  # Optional dimensions (e.g. x86-64-e4 CPUs on win-rel) are for CQ builders.
  for suite in suites:
    suite.spec = attr.evolve(suite.spec, optional_dimensions={})
  # Like test_patch(): the shard merge of coverage builders needs
  # llvm-profdata (crbug/1346781). The runner bootstraps the CQ builder's
  # $build/code_coverage, so the merge matches the instrumented binaries.
  # Coverage data isn't processed.
  if api.code_coverage.use_clang_coverage:
    api.code_coverage.ensure_clang_coverage_tools()

  checkout_dir = api.chromium_checkout.checkout_dir
  source_dir = api.chromium_checkout.source_dir
  build_dir = api.chromium.default_build_dir(source_dir)
  with api.chromium_tests.wrap_chromium_tests(
    checkout_dir, source_dir, build_dir, tests=suites
  ):
    invalid_suites, failing_suites = api.test_utils.run_tests_with_patch(
      checkout_dir,
      source_dir,
      build_dir,
      suites,
      retry_failed_shards=builder_config.retry_failed_shards,
    )
  invalid = sorted({s.name for s in invalid_suites})
  return invalid, sorted({s.name for s in failing_suites} - set(invalid))


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
    # FAILURE is reserved for false acceptances, which rts_complement_trigger
    # treats as covered.
    # TODO(crbug.com/535256440): skip these CQ builds in the trigger.
    if rts_status != 'SUCCESS':
      return result_pb.RawResult(
        status=common_pb.INFRA_FAILURE,
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

    # Queries the CQ builder's ResultDB variant for LUCI Analysis flake
    # exonerations, while uploading this build's results under its own
    # builder variant.
    api.test_utils.luci_analysis_builder = cq_build.builder.builder

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

    summary = (
      f'CQ build {cq_build.id} ({cq_build.builder.builder}), compilator build '
      f'{compilator_build.id}\n\n'
    )
    if not suites:
      return result_pb.RawResult(
        status=common_pb.SUCCESS,
        summary_markdown=summary + 'No suites to run',
      )

    try:
      invalid_suites, failed_suites = _run_complement_suites(
        api, builder_config, suites
      )
    # Test failures are returned, not raised; this is for infra failures like
    # failing to trigger or collect the tasks.
    except api.step.StepFailure as e:
      return result_pb.RawResult(
        status=common_pb.INFRA_FAILURE,
        summary_markdown=summary + f'Test phase failed: {e.reason}',
      )

  names = ', '.join(s.name for s in suites)
  summary += f'Ran the RTS skipped tests of {len(suites)} suites: {names}'
  # rts_complement_trigger treats SUCCESS and FAILURE runners as covered, so a
  # run where some suites have no results is an INFRA_FAILURE.
  if invalid_suites:
    summary += '\n\nSuites without valid results: ' + ', '.join(invalid_suites)
    return result_pb.RawResult(
      status=common_pb.INFRA_FAILURE, summary_markdown=summary
    )
  if failed_suites:
    step = api.step.empty(
      'record false acceptances', step_text=', '.join(failed_suites)
    )
    step.presentation.properties.update(
      json_format.MessageToDict(
        OutputProperties(failed_suites=failed_suites),
        preserving_proto_field_name=True,
      )
    )
    summary += '\n\nSuites with false acceptances: ' + ', '.join(failed_suites)
    return result_pb.RawResult(
      status=common_pb.FAILURE, summary_markdown=summary
    )
  return result_pb.RawResult(status=common_pb.SUCCESS, summary_markdown=summary)


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
        'swarming': {
          'dimensions': {'os': 'Linux', 'pool': 'luci.chromium.tests'},
          # Like win-rel's browser_tests and interactive_ui_tests.
          'optional_dimensions': {'30': {'cpu': 'x86-64-e4'}},
          'shards': shards,
        },
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

  browser_tests_trigger = (
    'test_pre_run (with patch).[trigger] browser_tests (with patch)'
  )

  def check_complement_request(check, request):
    check(request.priority == COMPLEMENT_PRIORITY)
    check(COMPLEMENT_TAG in request.tags)
    # No optional (e4) slice.
    check(len(request) == 1)
    check(request[0].expiration_secs == COMPLEMENT_EXPIRATION_SECS)
    check(request[0].dimensions['pool'] == 'luci.chromium.tests')
    check(
      '--filter=gen/rts/browser_tests_inverted.filter' in request[0].command
    )
    # Results are uploaded under rts-complement-runner's ResultDB variant.
    check('builder:rts-complement-runner' in request[0].command)

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
      api.swarming.check_triggered_request,
      browser_tests_trigger,
      check_complement_request,
    ),
    api.post_check(
      api.swarming.check_triggered_request,
      f'{browser_tests_trigger} (10)',
      check_complement_request,
    ),
    api.post_check(post_process.DoesNotRun, f'{browser_tests_trigger} (11)'),
    api.post_check(
      post_process.DoesNotRun,
      'test_pre_run (with patch).[trigger] content_unittests (with patch)',
    ),
    api.post_check(post_process.DoesNotRun, 'browser_tests (without patch)'),
    # Only the variants with unexpected results (-u), like in CQ.
    api.post_check(
      post_process.StepCommandContains,
      'collect tasks (with patch).browser_tests results',
      ['-u'],
    ),
    api.post_check(post_process.DoesNotRun, 'record false acceptances'),
    api.post_check(
      post_process.SummaryMarkdown,
      'CQ build 1000 (win-rel), compilator build 2000\n\n'
      'Ran the RTS skipped tests of 1 suites: browser_tests',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'false_acceptance',
    full_build(),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'browser_tests',
      'with patch',
      failures=['Test.One', 'Test.Flaky'],
      successes=['Test.Two'],
      expected_skips=['Test.DISABLED_Three'],
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'browser_tests',
      'retry shards with patch',
      failures=['Test.One', 'Test.Two'],
      successes=['Test.Flaky'],
    ),
    api.post_check(
      post_process.PropertyEquals, 'failed_suites', ['browser_tests']
    ),
    api.post_check(
      post_process.SummaryMarkdownRE,
      'Suites with false acceptances: browser_tests',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  # A structured test ID, like the CQ builders' with the
  # chromium_tests.resultdb_module experiment.
  flaky_test_id = '://chrome/test\\:browser_tests!gtest::Test#Flaky'
  flaky_result = test_result_pb.TestResult(
    test_id=flaky_test_id,
    tags=[{'key': 'test_name', 'value': 'Test.Flaky'}],
    status=test_result_pb.FAIL,
    # Uploaded under rts-complement-runner's ResultDB variant.
    variant=rdb_common_pb.Variant(
      **{
        'def': {
          'builder': 'rts-complement-runner',
          'os': 'Linux',
          'test_suite': 'browser_tests',
        }
      }
    ),
  )

  def check_stability_query(check, steps):
    stdin = steps['query LUCI Analysis for stability.rpc call'].stdin
    (variant,) = api.json.loads(stdin)['testVariants']
    check(variant['testId'] == flaky_test_id)
    # Queried under the CQ builder's ResultDB variant.
    check(
      variant['variant']
      == {
        'def': {
          'builder': 'win-rel',
          'os': 'Linux',
          'test_suite': 'browser_tests',
        }
      }
    )
    check('variantHash' not in variant)

  yield api.test(
    'known_flaky_failure',
    full_build(),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'browser_tests', 'with patch', failures=['Test.Flaky']
    ),
    # Replaces the helper's ninja:// results.
    api.override_step_data(
      'collect tasks (with patch).browser_tests results',
      stdout=api.raw_io.output_text(
        api.resultdb.serialize(
          {'inv': api.resultdb.Invocation(test_results=[flaky_result])}
        )
      ),
    ),
    api.override_step_data(
      'query LUCI Analysis for stability.rpc call',
      stdout=api.json.output(
        api.luci_analysis.generate_stability_response(
          [
            api.luci_analysis.generate_stability_analysis(
              test_id=flaky_test_id,
              flake_rate_is_met=True,
              run_flaky_verdicts_12h=1,
            )
          ]
        )
      ),
    ),
    api.post_check(check_stability_query),
    # Exonerated, so it's not retried and isn't a false acceptance.
    api.post_check(
      post_process.DoesNotRun, 'browser_tests (retry shards with patch)'
    ),
    api.post_check(post_process.DoesNotRun, 'record false acceptances'),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'invalid_suite',
    full_build(),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'browser_tests', 'with patch', invalid=True
    ),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'browser_tests', 'retry shards with patch', invalid=True
    ),
    api.post_check(
      post_process.SummaryMarkdownRE,
      'Suites without valid results: browser_tests',
    ),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'invalid_suite_valid_on_retry',
    full_build(),
    api.chromium_tests.gen_swarming_and_rdb_results(
      'browser_tests', 'with patch', invalid=True
    ),
    api.post_check(
      post_process.MustRun, 'browser_tests (retry shards with patch)'
    ),
    api.post_check(
      post_process.SummaryMarkdown,
      'CQ build 1000 (win-rel), compilator build 2000\n\n'
      'Ran the RTS skipped tests of 1 suites: browser_tests',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'clang_coverage',
    full_build(),
    api.code_coverage(use_clang_coverage=True),
    api.post_check(post_process.MustRun, 'ensure clang coverage tools'),
    api.post_check(post_process.DoesNotRun, 'process clang code coverage data'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'test_phase_failure',
    full_build(),
    # The first run has valid results, then triggering the shard retry fails.
    api.chromium_tests.gen_swarming_and_rdb_results(
      'browser_tests', 'with patch', failures=['Test.One']
    ),
    api.step_data(
      'test_pre_run (retry shards with patch).[trigger] browser_tests '
      '(retry shards with patch)',
      retcode=1,
    ),
    api.post_check(post_process.SummaryMarkdownRE, 'Test phase failed'),
    api.expect_status('INFRA_FAILURE'),
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
    api.post_check(
      post_process.SummaryMarkdown,
      'CQ build 1000 (win-rel), compilator build 2000\n\nNo suites to run',
    ),
    api.post_check(post_process.DoesNotRun, 'record false acceptances'),
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
    api.expect_status('INFRA_FAILURE'),
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
    api.expect_status('INFRA_FAILURE'),
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
