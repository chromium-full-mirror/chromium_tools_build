# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Runs the Mega CQ once with reduced retries.

Intended to be ran on a regular, nightly schedule to provide a signal into the
health of all optional trybots. This recipe is intended to be run from a
single Chrome CI builder. This differs from the Mega CQ orchestrator trybots
that developers actually trigger when they run the Mega CQ, as there's one
trybot for Chromium and one for Chrome. This is nominally so external devs
can still trigger the public half of the Mega CQ. There's no such need for this
smoke checker.
"""

from recipe_engine import post_process
from recipe_engine.config_types import Path
from recipe_engine.recipe_api import RecipeApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.recipe_engine.result import RawResult
from PB.recipes.build.chromium.mega_cq_smoke_check import InputProperties
from PB.go.chromium.org.luci.buildbucket.proto.builds_service import (
  BatchResponse,
)

from RECIPE_MODULES.build.chromium_tests import steps

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  chromium,
  chromium_gerrit_utils,
  chromium_mega_cq,
)
from RECIPE_MODULES.depot_tools import gitiles
from RECIPE_MODULES.recipe_engine import buildbucket, properties, step


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_gerrit_utils: chromium_gerrit_utils.API
  chromium_mega_cq: chromium_mega_cq.API
  gitiles: gitiles.API
  properties: properties.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  gitiles: gitiles.TEST_API
  properties: properties.TEST_API


PROPERTIES = InputProperties

GERRIT_TOPIC = 'mega-cq-smoke-check'


def RunSteps(api: RecipeApi, properties: InputProperties):
  trybots = []
  for bots_file in properties.bots_files:
    trybots.extend(
      api.chromium_mega_cq.read_bots_file(
        bots_file.repo, bots_file.file_path, branch='HEAD'
      )
    )

  gerrit_change, _ = api.chromium_gerrit_utils.create_temp_cl(
    # We choose a file in //base/ for the CL since it's a pretty fundamental
    # file and should get built into most targets. And it's not in the
    # analyze-exclusion list, which means GN-analyze won't get bypassed. This
    # is important since some trybots' analyze step can be broken, but that
    # wouldn't get exposed for CLs that touch files in the analyze-exclusion
    # list.
    'base/check.cc',
    GERRIT_TOPIC,
    git_footers=[steps.INCLUDE_CI_FOOTER + ': true'],
  )

  # Clean up any old CLs left around from previous runs. A mega CQ run might
  # take upwards of a day, so only remove CLs older than 2d.
  api.chromium_gerrit_utils.abandon_old_cls(GERRIT_TOPIC, '48h')

  result, failed_trybots = api.chromium_mega_cq.trigger_and_collect_bots(
    trybots, retries=0, gerrit_change=gerrit_change
  )
  api.chromium_gerrit_utils.abandon_cl(gerrit_change.change)
  if not failed_trybots:
    return result
  # Return our own markdown so we can see which trybots failed from the builder
  # page of the smoke-checker.
  summary_md_lines = [f'{len(failed_trybots)} builders failed:']
  for trybot in failed_trybots:
    summary_md_lines.append(' - ' + '/'.join(trybot))

  step_result = api.step.empty('record results')
  step_result.presentation.properties['failed_trybots'] = failed_trybots
  return RawResult(
    status=result.status, summary_markdown='\n'.join(summary_md_lines)
  )


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.properties(
      bots_files=[
        {
          'repo': 'https://chromium.googlesource.com/chromium/src',
          'file_path': 'infra/mega_cq_bots.txt',
        },
      ],
    ),
    api.chromium.ci_build(),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'with_failure',
    api.properties(
      bots_files=[
        {
          'repo': 'https://chromium.googlesource.com/chromium/src',
          'file_path': 'infra/mega_cq_bots.txt',
        },
      ],
    ),
    api.chromium.ci_build(),
    api.step_data(
      'get mega_cq_bots.txt.read mega_cq_bots.txt',
      api.gitiles.make_encoded_file(
        '\n'.join(
          [
            'chromium/try/some_bot',
          ]
        )
      ),
    ),
    api.buildbucket.simulated_schedule_output(
      BatchResponse(responses=[{'schedule_build': {'id': 100}}]),
      step_name='trigger some_bot.trigger (attempt 1)',
    ),
    api.buildbucket.simulated_collect_output(
      [
        api.buildbucket.ci_build_message(build_id=100, status='FAILURE'),
      ],
      step_name='trigger some_bot.collect (attempt 1)',
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
