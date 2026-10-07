# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Runs the RTS complement (inverse) tests of one CQ build.

Launched by chromium/rts_complement_trigger, one build per CQ build. It works
like chromium/orchestrator without the compile: instead of triggering a
compilator build, it reads the CQ build (cq_build_id) and the outputs of its
with patch compilator build (compilator_build_id). The builder config is the
one the CQ build used, the trigger copies it from that compilator build to this
build's $build/chromium_tests_builder_config.

Reuses chromium_orchestrator.configure_build() and
chromium_orchestrator.process_sub_build().

Skeleton: creating, running and recording the complement tests are stubs, so
no swarming test tasks are triggered.
"""

from dataclasses import dataclass

from recipe_engine import post_process
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.chromium.rts_complement_runner import InputProperties
from RECIPE_MODULES.build import (
  chromium,
  chromium_orchestrator,
  chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import buildbucket, properties, step

PROPERTIES = InputProperties


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_orchestrator: chromium_orchestrator.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_orchestrator: chromium_orchestrator.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  properties: properties.TEST_API


def _create_complement_tests(
  api: DEPS,
  cq_build: build_pb.Build,
  compilator_output: chromium_orchestrator.api.CompilatorOutputProps,
) -> list[str]:
  """Creates the complement tests of the actively filtered suites (stub).

  TODO(crbug.com/565109194): download the 'src-side deps' CAS bundle using
  `compilator_output.src_side_deps_digest` (so the runner doesn't need a full
  checkout), create the steps.Test objects using the test preparation steps of
  chromium_orchestrator.test_patch() (create_targets_config and
  process_swarming_props) with the 'rts_complement' command line variant, keep
  the suites RTS actively skipped tests of in `cq_build`, and scale shards by
  the skipped test count.
  """
  del cq_build, compilator_output
  api.step.empty('create complement tests (stub)')
  return []


def _run_complement_tests(
  api: DEPS,
  tests: list[str],
) -> list[str]:
  """Runs the complement tests and returns the failing suites (stub).

  TODO(crbug.com/565109194): set the ResultDB base variant builder to the CQ
  builder, lower chromium_swarming.default_priority below CQ, tag tasks with
  rts_run_type:complement, and run `tests` with
  test_utils.run_tests_with_patch() inside chromium_tests.wrap_chromium_tests()
  (retrying failed shards and exonerating known flaky failures with LUCI
  Analysis, without a without patch retry).
  """
  api.step.empty(
    'run complement tests (stub)',
    step_text=f'Would run {len(tests)} complement test suites',
  )
  return []


def _record_false_acceptances(
  api: DEPS,
  cq_build: build_pb.Build,
  failing_tests: list[str],
) -> None:
  """Records the skipped tests that fail, as data (stub).

  TODO(crbug.com/565109194): write the rts_complement output property with the
  per-suite test counts and false acceptances instead of failing this build.
  """
  api.step.empty(
    'record false acceptances (stub)',
    step_text=(
      f'{len(failing_tests)} failing suites for CQ build {cq_build.id}'
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
    compilator_build = api.buildbucket.get(
      props.compilator_build_id,
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

    api.chromium_orchestrator.configure_build()

    compilator_output, raw_result = api.chromium_orchestrator.process_sub_build(
      compilator_build, is_compile_phase=True, with_patch=True
    )
    # The compilator had nothing to trigger or failed.
    if raw_result:
      return raw_result

    tests = _create_complement_tests(api, cq_build, compilator_output)
    failing_tests = _run_complement_tests(api, tests)
    _record_false_acceptances(api, cq_build, failing_tests)

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
      + api.properties(
        InputProperties(cq_build_id=1000, compilator_build_id=2000)
      )
    )

  def cq_build():
    return api.buildbucket.simulated_get(
      build_pb.Build(
        id=1000,
        builder=dict(project='chromium', bucket='try', builder='win-rel'),
      ),
      step_name='read CQ build',
    )

  def compilator_build(include_swarming_props=True):
    output_properties = api.chromium_orchestrator.get_compilator_output_props(
      include_swarming_props=include_swarming_props,
      include_rts_props=True,
    )
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
    return api.buildbucket.simulated_get(
      build, step_name='read compilator build'
    )

  yield api.test(
    'basic',
    runner_build(),
    cq_build(),
    compilator_build(),
    api.post_check(post_process.MustRun, 'set rdb sources'),
    api.post_check(post_process.MustRun, 'create complement tests (stub)'),
    api.post_check(post_process.MustRun, 'run complement tests (stub)'),
    api.post_check(post_process.MustRun, 'record false acceptances (stub)'),
    api.post_check(
      post_process.SummaryMarkdown,
      'CQ build 1000 (win-rel), compilator build 2000',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_tests_to_trigger',
    runner_build(),
    cq_build(),
    compilator_build(include_swarming_props=False),
    api.post_check(post_process.DoesNotRun, 'create complement tests (stub)'),
    api.post_check(post_process.SummaryMarkdown, 'compilator summary'),
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
