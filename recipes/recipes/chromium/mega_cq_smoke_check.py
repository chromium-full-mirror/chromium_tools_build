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

from PB.recipes.build.chromium.mega_cq_smoke_check import InputProperties

DEPS = [
    'chromium',
    'chromium_gerrit_utils',
    'chromium_mega_cq',
    'recipe_engine/properties',
    'recipe_engine/step',
]

PROPERTIES = InputProperties

GERRIT_TOPIC = 'mega-cq-smoke-check'


def RunSteps(api: RecipeApi, properties: InputProperties):
  trybots = []
  for bots_file in properties.bots_files:
    trybots.extend(
        api.chromium_mega_cq.read_bots_file(
            bots_file.repo, bots_file.file_path, branch='HEAD'))

  api.chromium_gerrit_utils.create_temp_cl(
      # We choose a file in //base/ for the CL since it's a pretty fundamental
      # file and should get built into most targets. And it's not in the
      # analyze-exclusion list, which means GN-analyze won't get bypassed. This
      # is important since some trybots' analyze step can be broken, but that
      # wouldn't get exposed for CLs that touch files in the analyze-exclusion
      # list.
      'base/check.cc',
      GERRIT_TOPIC,
  )

  # Clean up any old CLs left around from previous runs. A mega CQ run might
  # take upwards of a day, so only remove CLs older than 2d.
  api.chromium_gerrit_utils.abandon_old_cls(GERRIT_TOPIC, '48h')

  # TODO(crbug.com/367181099): Actually trigger the trjobs.


def GenTests(api: RecipeTestApi):
  yield api.test(
      'basic',
      api.properties(
          bots_files=[
              {
                  'repo': 'https://chromium.googlesource.com/chromium/src',
                  'file_path': 'infra/mega_cq_bots.txt',
              },
          ],),
      api.chromium.ci_build(),
      api.post_process(post_process.DropExpectation),
  )
