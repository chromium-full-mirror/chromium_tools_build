# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Launches chromium/rts_complement_runner builds for recent CQ builds.

Finds the CQ builds of the allowlisted builders from the previous MTV calendar
day that ran RTS-filtered tests and archived RTS complement command lines,
skips CQ builds already covered by a passing runner build, queries
Gerrit to prioritize submitted patchsets, and runs up to
`MAX_CONCURRENT_BUILDS` runner builds at a time (retrying once on
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
from RECIPE_MODULES.depot_tools import gerrit
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
GERRIT_BATCH_SIZE = 100
LOOKBACK_HOURS = 24
MAX_CONCURRENT_BUILDS = 5
MAX_RETRIES = 1
MTV_TZ = datetime.timezone(datetime.timedelta(hours=-8))
# Mirrors LUCI CV's EquivalentPatchsetRange (cv/internal/gerrit/equi.go).
NO_CODE_CHANGE_KINDS = frozenset(
  {
    'NO_CHANGE',
    'NO_CODE_CHANGE',
    'MERGE_FIRST_PARENT_UPDATE',
    'TRIVIAL_REBASE',
    'TRIVIAL_REBASE_WITH_MESSAGE_UPDATE',
  }
)
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
  gerrit: gerrit.API
  led: led.API
  step: step.API
  time: time.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium_orchestrator: chromium_orchestrator.TEST_API
  gerrit: gerrit.TEST_API
  properties: properties.TEST_API


def _cq_time_range(api: DEPS, lookback_hours: int) -> common_pb.TimeRange:
  """Returns the `[00:00 MTV - lookback_hours, 00:00 MTV)` time range."""
  end = int(
    api.time.utcnow()
    .replace(tzinfo=datetime.timezone.utc)
    .astimezone(MTV_TZ)
    .replace(hour=0, minute=0, second=0, microsecond=0)
    .timestamp()
  )
  return common_pb.TimeRange(
    start_time=timestamp_pb2.Timestamp(seconds=end - lookback_hours * 3600),
    end_time=timestamp_pb2.Timestamp(seconds=end),
  )


def _find_cq_builds(
  api: DEPS,
  builders: Sequence[str],
  lookback_hours: int,
  cq_time_range: common_pb.TimeRange,
  search_limit: int,
) -> dict[str, list[build_pb.Build]]:
  """Finds the SUCCESS CQ builds of `builders` in the lookback window.

  Returns `{'win-rel': [Build(id=..., ...), ...], ...}`, newest first.
  """
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
            create_time=cq_time_range,
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


def _find_covered_cq_build_ids(
  api: DEPS,
  cq_time_range: common_pb.TimeRange,
  search_limit: int,
) -> set[int]:
  """Returns CQ build IDs already covered by a passing runner build."""
  # Runners are triggered after the CQ window ends, so only bound start_time.
  create_time = common_pb.TimeRange(start_time=cq_time_range.start_time)
  builder = dict(
    project=api.buildbucket.build.builder.project,
    bucket=api.buildbucket.build.builder.bucket,
    builder=RUNNER_BUILDER,
  )
  runner_builds = api.buildbucket.search_with_multiple_predicates(
    [
      builds_service_pb.BuildPredicate(
        builder=builder,
        # Runners report false acceptances as data (crbug.com/565109194) rather
        # than failing, so only SUCCESS means the complement tests ran.
        status=common_pb.SUCCESS,
        create_time=create_time,
      )
    ],
    limit=search_limit,
    step_name='find covered CQ builds',
    report_build=False,
    fields={'id', 'tags'},
  )
  covered_ids: set[int] = set()
  for build in runner_builds:
    for tag in build.tags:
      if tag.key == 'rts_complement_cq_build_id' and tag.value.isdigit():
        covered_ids.add(int(tag.value))
  return covered_ids


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


def _find_submitted_build_ids(
  api: DEPS,
  builds: Sequence[build_pb.Build],
) -> set[int]:
  """Returns IDs of CQ builds whose tested patchset was submitted in Gerrit.

  A build's patchset is considered submitted if its CL has status MERGED in
  Gerrit and every revision after `build.patchset` is in
  `NO_CODE_CHANGE_KINDS` (accounting for Gerrit's submit revision when CV
  reuses a green build from the prior patchset).
  """
  changes_by_host: dict[str, list[int]] = {}
  for b in builds:
    cl = b.input.gerrit_changes[0]
    changes_by_host.setdefault(cl.host, []).append(cl.change)

  merged_revisions: dict[tuple[str, int], Mapping[str, Any]] = {}
  for host, changes in changes_by_host.items():
    unique = list(dict.fromkeys(changes))
    # Batch queries to stay well below Gerrit's query-term and result limits.
    for i in range(0, len(unique), GERRIT_BATCH_SIZE):
      batch = unique[i : i + GERRIT_BATCH_SIZE]
      for info in api.gerrit.get_changes(
        f'https://{host}',
        query_params=[('change', ' OR change:'.join(str(c) for c in batch))],
        o_params=['ALL_REVISIONS'],
        limit=len(batch),
        name='find submitted CLs',
      ):
        if info.get('status') == 'MERGED':
          merged_revisions[(host, int(info['_number']))] = (
            info.get('revisions') or {}
          )

  submitted_ids: set[int] = set()
  for build in builds:
    cl = build.input.gerrit_changes[0]
    revisions = merged_revisions.get((cl.host, cl.change))
    if not revisions:
      continue
    has_later_code_change = any(
      int(rev['_number']) > cl.patchset
      and rev.get('kind') not in NO_CODE_CHANGE_KINDS
      for rev in revisions.values()
    )
    if not has_later_code_change:
      submitted_ids.add(build.id)
  return submitted_ids


def _select_cq_builds(
  api: DEPS,
  builds_by_builder: Mapping[str, Sequence[build_pb.Build]],
  covered_ids: set[int],
  limit: int,
) -> tuple[list[build_pb.Build], set[int]]:
  """Selects up to `limit` uncovered CQ builds with RTS complement.

  Keeps the newest build per `(host, change, patchset)` on each builder,
  filters out `covered_ids`, queries Gerrit to prioritize builds whose
  patchset was submitted (`submitted_ids`), and interleaves builders
  round-robin.
  """
  builder_queues: list[list[build_pb.Build]] = []
  uncovered_builds: list[build_pb.Build] = []
  missing_complement: list[int] = []
  found = 0
  with_complement = 0

  for builds in builds_by_builder.values():
    found += len(builds)
    eligible, missing = _eligible_builds(builds)
    missing_complement.extend(missing)
    with_complement += len(eligible)

    uncovered = [b for b in eligible if b.id not in covered_ids]
    builder_queues.append(uncovered)
    uncovered_builds.extend(uncovered)

  submitted_ids = _find_submitted_build_ids(api, uncovered_builds)
  for queue in builder_queues:
    queue.sort(key=lambda b: b.id not in submitted_ids)

  round_robin = [
    b
    for row in itertools.zip_longest(*builder_queues)
    for b in row
    if b is not None
  ]
  selected = sorted(
    round_robin,
    key=lambda b: b.id not in submitted_ids,
  )[:limit]

  select_step = api.step.empty(
    'select CQ builds',
    step_text=(
      f'{found} found, {with_complement} with RTS complement, '
      f'{len(uncovered_builds)} uncovered ({len(submitted_ids)} submitted), '
      f'{len(selected)} selected'
    ),
  )
  if missing_complement:
    select_step.presentation.status = api.step.FAILURE
    missing_ids = ', '.join(str(i) for i in missing_complement)
    select_step.presentation.step_text += (
      f' (missing {RTS_COMPLEMENT_VARIANT}: {missing_ids})'
    )
  return selected, submitted_ids


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
  submitted: bool,
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
                'rts_complement_submitted': str(submitted).lower(),
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

  cq_time_range = _cq_time_range(api, lookback_hours)
  builds_by_builder = _find_cq_builds(
    api, builders, lookback_hours, cq_time_range, search_limit
  )
  covered_ids = _find_covered_cq_build_ids(
    api, cq_time_range, search_limit * len(builders)
  )
  cq_builds, submitted_ids = _select_cq_builds(
    api, builds_by_builder, covered_ids, build_limit
  )
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
      results[cq_build.id] = _run_runner(
        api, cq_build, cq_build.id in submitted_ids
      )

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
      'submitted': b.id in submitted_ids,
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

  def covered_runner(runner_id, cq_build_id):
    return build_pb.Build(
      id=runner_id,
      tags=[
        common_pb.StringPair(
          key='rts_complement_cq_build_id', value=str(cq_build_id)
        )
      ],
    )

  def search(builder, builds):
    return api.buildbucket.simulated_multi_predicates_search_results(
      builds, step_name=f'{find_step}.search {builder}'
    )

  def covered(builds):
    return api.buildbucket.simulated_multi_predicates_search_results(
      builds, step_name='find covered CQ builds'
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
        cq_build(9, change=1009, patchset=1),
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
    covered([covered_runner(900, 9)]),
    api.override_step_data(
      'gerrit find submitted CLs',
      api.gerrit.get_multiple_changes_response_data(
        [
          api.gerrit.gerrit_change_data(
            change_number=1001,
            patchset=2,
            status='MERGED',
            revisions={
              '1': {'_number': 2, 'kind': 'REWORK'},
              '2': {'_number': 3, 'kind': 'NO_CODE_CHANGE'},
              '3': {'_number': 4, 'kind': 'MERGE_FIRST_PARENT_UPDATE'},
              '4': {'_number': 5, 'kind': 'NO_CHANGE'},
            },
          ),
          api.gerrit.gerrit_change_data(change_number=1004, patchset=2),
        ]
      ),
    ),
    api.buildbucket.simulated_collect_output(
      [
        api.buildbucket.ci_build_message(
          build_id=8922054662172514000, status='INFRA_FAILURE'
        )
      ],
      step_name='trigger win-rel (1).collect (attempt 1)',
    ),
    api.post_check(
      post_process.StepTextEquals,
      'select CQ builds',
      '8 found, 5 with RTS complement, 4 uncovered (1 submitted), '
      '4 selected (missing rts_complement: 2)',
    ),
    api.post_check(post_process.StepFailure, 'select CQ builds'),
    api.post_check(
      post_process.StepCommandContains,
      'find covered CQ builds',
      ['-n', '400'],
    ),
    api.post_check(
      post_process.PropertyEquals,
      'launched_runners',
      [
        {
          'cq_build_id': '1',
          'cq_builder': 'win-rel',
          'submitted': True,
          'runner_build_id': '8922054662172514001',
        },
        {
          'cq_build_id': '4',
          'cq_builder': 'mac-rel',
          'submitted': False,
          'runner_build_id': '8922054662172514002',
        },
        {
          'cq_build_id': '6',
          'cq_builder': 'win-rel',
          'submitted': False,
          'runner_build_id': '8922054662172514003',
        },
        {
          'cq_build_id': '8',
          'cq_builder': 'mac-rel',
          'submitted': False,
          'runner_build_id': '8922054662172514004',
        },
      ],
    ),
    api.post_check(
      post_process.LogContains,
      'trigger win-rel (1).trigger (attempt 1)',
      'request',
      [
        '"key": "rts_complement_cq_build_id"',
        '"value": "1"',
        '"key": "rts_complement_submitted"',
        '"value": "true"',
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
    search('win-rel', [cq_build(1, has_complement=False), cq_build(2)]),
    covered([covered_runner(900, 2)]),
    api.post_check(
      post_process.StepTextEquals,
      'select CQ builds',
      '2 found, 1 with RTS complement, 0 uncovered (0 submitted), '
      '0 selected (missing rts_complement: 1)',
    ),
    api.post_check(post_process.DoesNotRun, 'gerrit find submitted CLs'),
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
