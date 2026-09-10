# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import datetime

from recipe_engine import recipe_api
from recipe_engine.engine_types import ResourceCost
from PB.recipe_engine.result import RawResult
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.go.chromium.org.luci.buildbucket.proto.builds_service import (
  BatchResponse,
)


class ChromiumMegaCqApi(recipe_api.RecipeApi):
  def read_bots_file(
    self, repo: str, file_path: str, *, branch: str = None
  ) -> list[tuple[str, str, str]]:
    """Curls the given mega_cq bots file from the repo.

    Args:
      repo: URL of the repo to fetch from.
      file_path: File path in the repo to the mega_cq bots file.
      branch: Branch of the repo to fetch from. Will use the CL-under-test's
        branch by default.

    Return: list of tuples for each trybot in form of (project, bucket, builder)
    """
    # Just curl the file to avoid needing a checkout. This means additions to
    # the mega CQ can't be tested in the CL that adds them via the mega CQ. We
    # assume this isn't a big deal.
    with self.m.step.nest('get mega_cq_bots.txt') as nest_step:
      mega_cq_bots_lines = self.m.gitiles.download_file(
        repo,
        file_path,
        branch=branch or self.m.tryserver.gerrit_change_target_ref,
        step_name='read mega_cq_bots.txt',
        step_test_data=lambda: self.m.gitiles.test_api.make_encoded_file(
          'chromium/try/bot1\nchromium/try/bot2'
        ),
      ).splitlines()
      nest_step.logs['bots'] = mega_cq_bots_lines

    trybots = []
    for line in mega_cq_bots_lines:
      project, bucket, builder = line.split('/')
      trybots.append((project, bucket, builder))
    return trybots

  def sleep_until_off_peak(self):
    # Use a resource script so we can get vpython to fetch "pytz", which will
    # handle timezones for us.
    step_result = self.m.step(
      'get time',
      ['vpython3', self.resource('print_us_pac_time.py')],
      stdout=self.m.raw_io.output_text(),
      step_test_data=lambda: self.m.raw_io.test_api.stream_output_text(
        '2023-10-23 12:00:00.000000-07:00'
      ),  # Noon on a Mon.
    )
    now = datetime.datetime.fromisoformat(step_result.stdout.strip())
    # We consider "peak" between 4:00 AM and 8:00 PM Pacific. 4:00 is quite
    # early, but that should avoid mega CQ runs triggered at 3:59 AM from
    # impacting normal CQ traffic during the real peak times.
    peak_start = datetime.datetime.combine(
      now.date(), datetime.time(hour=4), tzinfo=now.tzinfo
    )
    peak_end = datetime.datetime.combine(
      now.date(), datetime.time(hour=20), tzinfo=now.tzinfo
    )
    if now.weekday() < 5 and peak_start < now < peak_end:
      gerrit_change = self.m.tryserver.gerrit_change
      diff_s = (peak_end - now).seconds
      self.m.step(
        'need to wait for off-peak hours; sleeping for %s'
        % datetime.timedelta(seconds=diff_s),
        None,
      )
      self.m.time.sleep(diff_s)
      # We've seen CLs get deleted after triggering the mega CQ but before the CQ
      # wakes up from its sleep. So make sure the CL still exists before
      # proceeding.
      cls = self.m.gerrit.get_changes(
        'https://%s' % gerrit_change.host,
        query_params=[('change', str(gerrit_change.change))],
        o_params=['ALL_REVISIONS', 'ALL_COMMITS'],
        limit=1,
      )
      if not cls:
        raise self.m.step.StepFailure('CL no longer present on Gerrit')
    else:
      self.m.step('no sleep needed', None)

  def trigger_and_collect_bots(
    self,
    trybots: list[tuple[str, str, str]],
    *,
    retries: int = 2,
    gerrit_change: common_pb.GerritChange = None,
  ) -> tuple[RawResult, list[str]]:
    """Triggers all given trybots.

    Will trigger builds with adjusted priority and timeout.

    Args:
      trybots: list of tuples for each trybot in form of
        (project, bucket, builder)
      retries: Number of times to retry any failed tryjobs. Defaults to 2.
      gerrit_change: buildbucket.common.GerritChange of the CL to test. Defaults
        to current CL-under-test if not specified.

    Returns tuple of (combined RawResult for all tryjobs, list of trybots that
      failed)
    """

    def _run_bot(b, led_build):
      project, bucket, builder = b
      with self.m.step.nest('trigger ' + builder):
        # Increase the default priority + timeout + expiration since we expect
        # mega CQ builds to take longer.
        per_build_expiration_s = 12 * 60 * 60
        per_build_timeout_s = 6 * 60 * 60

        def _make_req():
          tags = {'mega_cq_build': '1'}
          # CV recipe module reads both tags and props, so need to propagate both
          # from parent build to children.
          for t in self.m.buildbucket.build.tags:
            if t.key.startswith(('cq', 'cv')):
              tags[t.key] = t.value
          buildbucket_schedule_kwargs = {}
          if gerrit_change:
            # The buildbucket module uses a special default val for
            # gerrit_changes, so only pass it down if it's not None for us.
            buildbucket_schedule_kwargs['gerrit_changes'] = [gerrit_change]
          req = self.m.buildbucket.schedule_request(
            builder,
            project=project,
            bucket=bucket,
            priority=self.m.buildbucket.swarming_priority + 10,
            tags=self.m.buildbucket.tags(**tags),
            inherit_buildsets=False,
            properties=self.m.cv.props_for_child_build,
            as_shadow_if_parent_is_led=True,
            # This ensures the triggered builds will get canceled if this
            # build ends
            swarming_parent_run_id=self.m.swarming.task_id,
            **buildbucket_schedule_kwargs,
          )
          req.scheduling_timeout.FromSeconds(per_build_expiration_s)
          req.execution_timeout.FromSeconds(per_build_timeout_s)
          return req

        for i in range(1, 2 + retries):
          if led_build:
            led_result = (
              self.m.chromium_orchestrator.trigger_led_recipe_bundled_build(
                priority=self.m.buildbucket.swarming_priority + 10,
                builder=builder,
                gerrit_change=gerrit_change,
              )
            )
            build_id = led_result.launch_result.build_id

          else:
            # Buildbucket de-dupes when using the exact same request object. So need
            # to create a new one each time.
            req = _make_req()
            build = self.m.buildbucket.schedule(
              [req],
              step_name=f'trigger (attempt {i})',
              # Merging all sub-builds' test results into a single invocation is
              # too much for RDB. So don't bother. Gerrit should still show all
              # results in the checks tab.
              include_sub_invs=False,
            )[0]
            self.m.cv.record_triggered_builds(build)
            build_id = build.id
          result = self.m.buildbucket.collect_build(
            build_id,
            step_name=f'collect (attempt {i})',
            # Mark the step as resource-free so it uncaps the amount of parallel
            # collects that can run. The step has minimal machine impact, so this
            # should be fine.
            cost=ResourceCost.zero(),
            timeout=per_build_expiration_s + per_build_timeout_s,
          )
          if result.status == common_pb.SUCCESS:
            return result
        return result

    workers = {}
    for b in trybots:
      workers[b] = self.m.futures.spawn_immediate(
        _run_bot, b, self.m.led.led_build
      )
    self.m.futures.wait(workers.values())

    total_success = 0
    total_failure = 0
    failed_trybots = []
    for trybot, worker in workers.items():
      result = worker.result()
      if result.status == common_pb.SUCCESS:
        total_success += 1
      else:
        total_failure += 1
        failed_trybots.append(trybot)
        step_result = self.m.step(result.builder.builder + ' failed', cmd=None)
        step_result.presentation.links[str(result.id)] = (
          self.m.buildbucket.build_url(build_id=result.id)
        )
        step_result.presentation.status = self.m.step.FAILURE

    summary_md = '<br/>'.join(
      [
        '%d builders succeeded' % total_success,
        '%d builders failed' % total_failure,
      ]
    )
    overall_status = common_pb.SUCCESS
    if total_failure:
      overall_status = common_pb.FAILURE
      # We retry each builder so many times, no need to have the CQ retry us.
      self.m.cv.set_do_not_retry_build()
    return (
      RawResult(status=overall_status, summary_markdown=summary_md),
      failed_trybots,
    )
