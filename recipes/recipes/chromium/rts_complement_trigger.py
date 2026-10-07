# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Launches chromium/rts_complement_runner builds for recent CQ builds.

Finds the CQ builds of the allowlisted builders that actively skipped tests
with RTS and launches one runner build per CQ build. It doesn't wait for the
runners, how many run at once is capped by the runner builder
(max_concurrent_builds).

Each runner gets its CQ build and the with patch compilator build of that CQ
build, along with the $build/chromium_tests_builder_config and
$build/code_coverage that compilator got from the CQ build, so the runner can
use chromium_orchestrator.configure_build() as is.

Skeleton: finding CQ and compilator builds, selecting CQ builds, and scheduling
runner builds are stubs.
"""

from dataclasses import dataclass

from recipe_engine import post_process
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.chromium.rts_complement_trigger import InputProperties
from RECIPE_MODULES.recipe_engine import properties, step

PROPERTIES = InputProperties

BUILD_LIMIT = 5
LOOKBACK_HOURS = 24
SEARCH_LIMIT = 200
RUNNER_BUILDER = 'rts-complement-runner'
# The CQ builders where RTS actively skips tests (enable_rts_filtering).
# Temporary hardcoded list; in subsequent CLs this will be obtained dynamically.
DEFAULT_BUILDERS = (
  'win-rel',
  'mac-rel',
  'android-x64-rel',
  'android-x86-rel',
  'android-desktop-x64-rel',
)


@dataclass
class DEPS(RecipeScriptApi):
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  properties: properties.TEST_API


def _find_cq_builds(
  api: DEPS,
  builders: list[str],
  lookback_hours: int,
  search_limit: int,
) -> dict[str, list[build_pb.Build]]:
  """Finds the SUCCESS CQ builds of `builders` in the lookback window (stub).

  TODO(crbug.com/565109194): query Buildbucket with
  search_with_multiple_predicates() for SUCCESS CQ builds (user_agent:cq,
  cq_experimental:false) of each builder in the lookback window, up to
  `search_limit` builds per builder, newest first.
  """
  api.step.empty(
    f'find CQ builds from last {lookback_hours} hours (stub)',
    step_text=(
      f'Would search {len(builders)} builders over last {lookback_hours}h '
      f'(search_limit={search_limit})'
    ),
  )
  return {}


def _select_cq_builds(
  api: DEPS,
  builds_by_builder: dict[str, list[build_pb.Build]],
  limit: int,
) -> list[build_pb.Build]:
  """Selects up to `limit` CQ builds that actively skipped tests (stub).

  TODO(crbug.com/565109194): query recent `RUNNER_BUILDER` builds in the
  window, read the `rts_complement_cq_build_id:<cq_build_id>` tag, and skip any
  CQ build ID that already has a SCHEDULED, STARTED, or SUCCESS runner build
  (allowing INFRA_FAILURE or CANCELED runner builds to be retried). Keep
  single-CL builds with rts_safety_summary.total_rts_skipped_tests > 0, taking
  the newest builds of each builder in turn so every builder gets coverage, up
  to `limit`.
  """
  api.step.empty(
    'select CQ builds (stub)',
    step_text=(
      f'Would select up to {limit} CQ builds from '
      f'{len(builds_by_builder)} builders'
    ),
  )
  return []


def _find_compilators(
  api: DEPS,
  cq_builds: list[build_pb.Build],
) -> dict[int, build_pb.Build]:
  """Finds the with patch compilator build of each CQ build (stub).

  TODO(crbug.com/565109194): query Buildbucket with
  BuildPredicate(child_of=...) for each CQ build and keep its with patch
  compilator build (skipping without patch compilators that set test_targets),
  keyed by compilator.ancestor_ids[-1].
  """
  api.step.empty(
    'find compilator builds (stub)',
    step_text=(
      f'Would find with patch compilators for {len(cq_builds)} CQ builds'
    ),
  )
  return {}


def _launch_runners(
  api: DEPS,
  cq_builds: list[build_pb.Build],
  compilators: dict[int, build_pb.Build],
) -> list[build_pb.Build]:
  """Schedules the runner builds, which can outlive this build (stub).

  TODO(crbug.com/565109194): schedule one `RUNNER_BUILDER` build per CQ build
  with buildbucket.schedule_request(can_outlive_parent=True,
  inherit_buildsets=False) and buildbucket.schedule(include_sub_invs=False),
  tagging it with `rts_complement_cq_build_id:<cq_build_id>`, passing the CQ
  build's gerrit_changes, and forwarding cq_build_id, compilator_build_id,
  $build/chromium_tests_builder_config, and $build/code_coverage from its
  compilator build.
  """
  api.step.empty(
    'launch runner builds (stub)',
    step_text=(
      f'Would schedule {RUNNER_BUILDER} builds for {len(cq_builds)} CQ '
      f'builds ({len(compilators)} compilators)'
    ),
  )
  return []


def RunSteps(api: DEPS, props: InputProperties) -> result_pb.RawResult:
  builders = list(props.builders or DEFAULT_BUILDERS)
  lookback_hours = props.lookback_hours or LOOKBACK_HOURS
  build_limit = props.build_limit or BUILD_LIMIT
  search_limit = props.search_limit or SEARCH_LIMIT

  builds_by_builder = _find_cq_builds(
    api, builders, lookback_hours, search_limit
  )
  cq_builds = _select_cq_builds(api, builds_by_builder, build_limit)
  compilators = _find_compilators(api, cq_builds)
  launched = _launch_runners(api, cq_builds, compilators)

  return result_pb.RawResult(
    status=common_pb.SUCCESS,
    summary_markdown=(
      f'CQ builds selected: {len(cq_builds)} (last {lookback_hours}h)\n\n'
      f'Runners launched: {len(launched)}'
    ),
  )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.post_check(
      post_process.MustRun, 'find CQ builds from last 24 hours (stub)'
    ),
    api.post_check(post_process.MustRun, 'select CQ builds (stub)'),
    api.post_check(post_process.MustRun, 'find compilator builds (stub)'),
    api.post_check(post_process.MustRun, 'launch runner builds (stub)'),
    api.post_check(
      post_process.SummaryMarkdown,
      'CQ builds selected: 0 (last 24h)\n\nRunners launched: 0',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'custom_properties',
    api.properties(
      InputProperties(
        builders=['win-rel', 'mac-rel'],
        lookback_hours=48,
        build_limit=1,
        search_limit=50,
      )
    ),
    api.post_check(
      post_process.StepTextContains,
      'find CQ builds from last 48 hours (stub)',
      ['Would search 2 builders over last 48h (search_limit=50)'],
    ),
    api.post_check(
      post_process.StepTextContains,
      'select CQ builds (stub)',
      ['Would select up to 1 CQ builds'],
    ),
    api.post_check(
      post_process.SummaryMarkdown,
      'CQ builds selected: 0 (last 48h)\n\nRunners launched: 0',
    ),
    api.post_process(post_process.DropExpectation),
  )
