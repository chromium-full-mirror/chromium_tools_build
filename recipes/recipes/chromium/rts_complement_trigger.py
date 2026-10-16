# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Launches chromium/rts_complement_runner builds for recent CQ builds.

Finds the CQ builds of the allowlisted builders from the previous MTV calendar
day that ran RTS-filtered tests and archived RTS complement command lines, and
runs up to `MAX_CONCURRENT_BUILDS` runner builds at a time (retrying once on
`INFRA_FAILURE`).

Each runner gets its CQ orchestrator build ID (`cq_build_id`) and the CQ
build's `$bootstrap/properties` so the polymorphic runner can bootstrap the
target CQ builder's config and resolve its compilator build.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import datetime
import itertools
from typing import Any

from google.protobuf import json_format, timestamp_pb2

from recipe_engine import post_process
from recipe_engine.engine_types import ResourceCost
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb
from PB.go.chromium.org.luci.buildbucket.proto import (
  builds_service as builds_service_pb,
)
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.chromium.rts_complement_trigger import InputProperties
from RECIPE_MODULES.build import chromium_orchestrator
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  futures,
  led,
  properties,
  step,
  time,
)

PROPERTIES = InputProperties

BUILD_LIMIT = 5
CQ_BUCKET = 'try'
LOOKBACK_HOURS = 24
MAX_CONCURRENT_BUILDS = 5
MAX_RETRIES = 1
MTV_TZ = datetime.timezone(datetime.timedelta(hours=-8))
RTS_COMPLEMENT_VARIANT = 'rts_complement'
SEARCH_LIMIT = 200
RUNNER_BUILDER = 'rts-complement-runner'
RUNNER_RECIPE = 'chromium/rts_complement_runner'
# Added to the template builder's priority under led, higher means less
# important.
LED_PRIORITY_ADJUSTMENT = 20
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
  buildbucket: buildbucket.API
  chromium_orchestrator: chromium_orchestrator.API
  futures: futures.API
  led: led.API
  step: step.API
  time: time.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium_orchestrator: chromium_orchestrator.TEST_API
  properties: properties.TEST_API


def _find_cq_builds(
  api: DEPS,
  builders: Sequence[str],
  lookback_hours: int,
  search_limit: int,
) -> dict[str, list[build_pb.Build]]:
  """Finds the SUCCESS CQ builds of `builders` in the lookback window.

  Returns `{'win-rel': [Build(id=..., ...), ...], ...}`, newest first.
  """
  end = int(
    api.time.utcnow()
    .replace(tzinfo=datetime.timezone.utc)
    .astimezone(MTV_TZ)
    .replace(hour=0, minute=0, second=0, microsecond=0)
    .timestamp()
  )
  create_time = common_pb.TimeRange(
    start_time=timestamp_pb2.Timestamp(seconds=end - lookback_hours * 3600),
    end_time=timestamp_pb2.Timestamp(seconds=end),
  )
  tags = api.buildbucket.tags(
    user_agent='cq', cq_experimental='false', rts_was_used='true'
  )
  with api.step.nest(f'find CQ builds from last {lookback_hours} hours'):
    return {
      builder: api.buildbucket.search_with_multiple_predicates(
        [
          builds_service_pb.BuildPredicate(
            builder=dict(
              project=api.buildbucket.build.builder.project,
              bucket=CQ_BUCKET,
              builder=builder,
            ),
            status=common_pb.SUCCESS,
            tags=tags,
            create_time=create_time,
          )
        ],
        limit=search_limit,
        step_name=f'search {builder}',
        report_build=False,
        fields={
          'id',
          'builder',
          'input.gerrit_changes',
          'input.properties',
          'output.properties',
        },
      )
      for builder in builders
    }


def _has_rts_complement(build: build_pb.Build) -> bool:
  """Returns True if the CQ build archived RTS complement command lines."""
  output_properties = json_format.MessageToDict(build.output.properties)
  return RTS_COMPLEMENT_VARIANT in output_properties.get(
    'swarming_command_lines_variant_digests', {}
  )


def _eligible_builds(
  builds: Sequence[build_pb.Build],
) -> tuple[list[build_pb.Build], list[int]]:
  """Returns single-CL builds with RTS complement, newest per patchset."""
  unique: dict[tuple[str, int, int], build_pb.Build] = {}
  missing_complement: list[int] = []
  for b in builds:
    if not _has_rts_complement(b):
      missing_complement.append(b.id)
      continue
    if len(b.input.gerrit_changes) == 1:
      cl = b.input.gerrit_changes[0]
      unique.setdefault((cl.host, cl.change, cl.patchset), b)
  return list(unique.values()), missing_complement


def _select_cq_builds(
  api: DEPS,
  builds_by_builder: Mapping[str, Sequence[build_pb.Build]],
  limit: int,
) -> list[build_pb.Build]:
  """Selects up to `limit` CQ builds with RTS complement command lines.

  Keeps the newest build per `(host, change, patchset)` on each builder
  and interleaves builders round-robin.
  """
  # TODO(crbug.com/535255761): Deduplicate already-covered CQ builds and query
  # Gerrit to prioritize submitted patchsets.
  queues: list[list[build_pb.Build]] = []
  missing_complement: list[int] = []
  for builds in builds_by_builder.values():
    eligible, missing = _eligible_builds(builds)
    queues.append(eligible)
    missing_complement.extend(missing)
  found = sum(len(builds) for builds in builds_by_builder.values())
  active = sum(len(q) for q in queues)
  selected = [
    b for row in itertools.zip_longest(*queues) for b in row if b is not None
  ][:limit]

  select_step = api.step.empty(
    'select CQ builds',
    step_text=(
      f'{found} found, {active} with RTS complement, {len(selected)} selected'
    ),
  )
  if missing_complement:
    select_step.presentation.status = api.step.FAILURE
    missing_ids = ', '.join(str(i) for i in missing_complement)
    select_step.presentation.step_text += (
      f' (missing {RTS_COMPLEMENT_VARIANT}: {missing_ids})'
    )
  return selected


def _runner_properties(cq_build: build_pb.Build) -> dict[str, Any]:
  """Returns the input properties dict for a runner build."""
  cq_props = json_format.MessageToDict(cq_build.input.properties)
  props: dict[str, Any] = {'cq_build_id': str(cq_build.id)}
  if '$bootstrap/properties' in cq_props:
    props['$bootstrap/properties'] = cq_props['$bootstrap/properties']
  return props


def _run_runner(
  api: DEPS,
  cq_build: build_pb.Build,
) -> int | None:
  """Triggers, collects, and retries on infra failure one runner build."""
  with api.step.nest(f'trigger {cq_build.builder.builder} ({cq_build.id})'):
    for attempt in range(1, 2 + MAX_RETRIES):
      try:
        if api.led.launched_by_led:
          led_result = (
            api.chromium_orchestrator.trigger_led_recipe_bundled_build(
              priority=LED_PRIORITY_ADJUSTMENT,
              builder=RUNNER_BUILDER,
              properties={
                **_runner_properties(cq_build),
                'recipe': RUNNER_RECIPE,
              },
              gerrit_change=cq_build.input.gerrit_changes[0],
            )
          )
          build_id = led_result.launch_result.build_id
        else:
          req = api.buildbucket.schedule_request(
            builder=RUNNER_BUILDER,
            properties=_runner_properties(cq_build),
            gerrit_changes=[cq_build.input.gerrit_changes[0]],
            tags=api.buildbucket.tags(
              **{
                'hide-in-gerrit': 'pointless',
                'skip-retry-in-gerrit': 'pointless',
                'rts_complement_cq_build_id': str(cq_build.id),
              }
            ),
            inherit_buildsets=False,
            can_outlive_parent=False,
          )
          build_id = api.buildbucket.schedule(
            [req],
            step_name=f'trigger (attempt {attempt})',
            include_sub_invs=False,
          )[0].id

        result = api.buildbucket.collect_build(
          build_id,
          step_name=f'collect (attempt {attempt})',
          cost=ResourceCost.zero(),
        )
        if result.status != common_pb.INFRA_FAILURE:
          return result.id if result.status == common_pb.SUCCESS else None
      except api.step.StepFailure:
        pass
    return None


def RunSteps(api: DEPS, props: InputProperties) -> result_pb.RawResult:
  if api.led.launched_by_led and not api.led.led_build:
    return result_pb.RawResult(
      status=common_pb.INFRA_FAILURE,
      summary_markdown=(
        'using led swarming tasks is not supported for this recipe,'
        ' please pass the -real-build flag to led get-* and led launch'
      ),
    )

  builders = list(props.builders or DEFAULT_BUILDERS)
  lookback_hours = props.lookback_hours or LOOKBACK_HOURS
  build_limit = props.build_limit or BUILD_LIMIT
  search_limit = props.search_limit or SEARCH_LIMIT

  builds_by_builder = _find_cq_builds(
    api, builders, lookback_hours, search_limit
  )
  cq_builds = _select_cq_builds(api, builds_by_builder, build_limit)
  if not cq_builds:
    return result_pb.RawResult(
      status=common_pb.SUCCESS,
      summary_markdown=(
        f'No CQ builds with RTS complement in the last {lookback_hours}h'
      ),
    )

  queue = iter(cq_builds)
  results: dict[int, int | None] = {}

  def _worker() -> None:
    for cq_build in queue:
      results[cq_build.id] = _run_runner(api, cq_build)

  workers = [
    api.futures.spawn_immediate(_worker)
    for _ in range(min(len(cq_builds), MAX_CONCURRENT_BUILDS))
  ]
  for w in workers:
    w.result()

  launched = [
    {
      'cq_build_id': str(b.id),
      'cq_builder': b.builder.builder,
      'runner_build_id': str(results[b.id]),
    }
    for b in cq_builds
    if results[b.id] is not None
  ]
  failed_launches = [b.id for b in cq_builds if results[b.id] is None]

  record = api.step.empty('record launched runners')
  record.presentation.properties['launched_runners'] = launched

  summary = [
    f'CQ builds selected: {len(cq_builds)} (last {lookback_hours}h)',
    f'Runners launched: {len(launched)}',
  ]
  if failed_launches:
    summary.append(
      'Failed to launch runners for: '
      + ', '.join(str(i) for i in failed_launches)
    )
  return result_pb.RawResult(
    status=common_pb.FAILURE if failed_launches else common_pb.SUCCESS,
    summary_markdown='\n\n'.join(summary),
  )


def GenTests(api: TEST_DEPS):
  find_step = 'find CQ builds from last 24 hours'

  def cq_build(
    build_id,
    builder='win-rel',
    has_complement=True,
    changes=1,
    change=None,
    patchset=1,
    bootstrap=True,
  ):
    base_change = change if change is not None else 1000 + build_id
    build = build_pb.Build(
      id=build_id,
      builder=dict(project='chromium', bucket='try', builder=builder),
      input=dict(
        gerrit_changes=[
          dict(
            host='chromium-review.googlesource.com',
            project='chromium/src',
            change=base_change + i,
            patchset=patchset,
          )
          for i in range(changes)
        ]
      ),
    )
    if bootstrap:
      build.input.properties.update(
        {
          '$bootstrap/properties': {
            'properties_file': (
              f'infra/config/generated/builders/try/{builder}/properties.json'
            ),
          }
        }
      )
    if has_complement:
      build.output.properties.update(
        {
          'swarming_command_lines_variant_digests': {
            RTS_COMPLEMENT_VARIANT: 'deadbeef/42',
          }
        }
      )
    return build

  def search(builder, builds):
    return api.buildbucket.simulated_multi_predicates_search_results(
      builds, step_name=f'{find_step}.search {builder}'
    )

  trigger_build = api.buildbucket.ci_build(
    project='chromium', bucket='try', builder='rts-complement-trigger'
  )

  yield api.test(
    'basic',
    trigger_build,
    api.properties(InputProperties(builders=['win-rel', 'mac-rel'])),
    search(
      'win-rel',
      [
        cq_build(6, change=1001, patchset=1),
        cq_build(1, change=1001, patchset=2),
        cq_build(7, change=1001, patchset=1),
        cq_build(2, has_complement=False),
      ],
    ),
    search(
      'mac-rel',
      [
        cq_build(4, 'mac-rel', change=1004, patchset=2, bootstrap=False),
        cq_build(8, 'mac-rel', change=1004, patchset=1),
        cq_build(5, 'mac-rel', changes=2),
      ],
    ),
    api.buildbucket.simulated_collect_output(
      [
        api.buildbucket.ci_build_message(
          build_id=8922054662172514000, status='INFRA_FAILURE'
        )
      ],
      step_name='trigger win-rel (6).collect (attempt 1)',
    ),
    api.post_check(
      post_process.StepTextEquals,
      'select CQ builds',
      '7 found, 4 with RTS complement, 4 selected (missing rts_complement: 2)',
    ),
    api.post_check(post_process.StepFailure, 'select CQ builds'),
    api.post_check(
      post_process.PropertyEquals,
      'launched_runners',
      [
        {
          'cq_build_id': '6',
          'cq_builder': 'win-rel',
          'runner_build_id': '8922054662172514001',
        },
        {
          'cq_build_id': '4',
          'cq_builder': 'mac-rel',
          'runner_build_id': '8922054662172514002',
        },
        {
          'cq_build_id': '1',
          'cq_builder': 'win-rel',
          'runner_build_id': '8922054662172514003',
        },
        {
          'cq_build_id': '8',
          'cq_builder': 'mac-rel',
          'runner_build_id': '8922054662172514004',
        },
      ],
    ),
    api.post_check(
      post_process.SummaryMarkdown,
      'CQ builds selected: 4 (last 24h)\n\nRunners launched: 4',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_active_builds',
    trigger_build,
    api.properties(InputProperties(builders=['win-rel'])),
    search('win-rel', [cq_build(1, has_complement=False)]),
    api.post_check(post_process.DoesNotRun, 'record launched runners'),
    api.post_check(
      post_process.SummaryMarkdown,
      'No CQ builds with RTS complement in the last 24h',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'led',
    api.buildbucket.try_build(
      project='chromium', bucket='try.shadow', builder='win-rel'
    ),
    api.properties(
      InputProperties(builders=['win-rel'], build_limit=2),
      **{'$recipe_engine/led': {'shadowed_bucket': 'try'}},
    ),
    api.chromium_orchestrator.override_led_get_builder(),
    search('win-rel', [cq_build(1), cq_build(2)]),
    api.buildbucket.simulated_collect_output(
      [
        api.buildbucket.ci_build_message(
          build_id=87654321, status='INFRA_FAILURE'
        )
      ],
      step_name='trigger win-rel (2).collect (attempt 1)',
    ),
    api.step_data('trigger win-rel (2).led launch (2)', retcode=1),
    api.post_check(
      post_process.StepCommandContains,
      'trigger win-rel (1).led get-builder',
      ['-adjust-priority', '20', f'luci.chromium.try:{RUNNER_BUILDER}'],
    ),
    api.post_check(
      post_process.SummaryMarkdown,
      'CQ builds selected: 2 (last 24h)\n\n'
      'Runners launched: 1\n\n'
      'Failed to launch runners for: 2',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'led_raw_swarming_task',
    api.properties(
      **{'$recipe_engine/led': {'led_run_id': 'chromium/led/user/abc'}}
    ),
    api.post_check(
      post_process.SummaryMarkdownRE,
      'using led swarming tasks is not supported for this recipe',
    ),
    api.expect_status('INFRA_FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
