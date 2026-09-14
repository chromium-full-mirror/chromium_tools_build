# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from dataclasses import dataclass

from recipe_engine.post_process import (
  DropExpectation,
  MustRun,
  StatusSuccess,
  StepCommandContains,
  StepFailure,
)
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import gn
from RECIPE_MODULES.recipe_engine import path, step


@dataclass
class DEPS(RecipeScriptApi):
  gn: gn.API
  path: path.API
  step: step.API


def RunSteps(api: DEPS):
  build_dir = api.path.cache_dir / 'builder' / 'src' / 'out' / 'Release'
  api.gn.check(build_dir)
  api.gn.check(
    build_dir,
    check_generated=True,
    step_name='gn check --check-generated',
  )
  # raise_on_failure=False lets the recipe continue while the step is still
  # reported as a failure.
  api.gn.check(
    build_dir,
    check_generated=True,
    step_name='gn check --check-generated (non-fatal)',
    raise_on_failure=False,
  )
  api.step.empty('after gn check')


def GenTests(api: RecipeTestApi):
  yield api.test(
    'basic',
    api.post_process(
      StepCommandContains,
      'gn check',
      [
        'RECIPE_REPO[depot_tools]/gn.py',
        'check',
        '[CACHE]/builder/src/out/Release',
      ],
    ),
    api.post_process(
      StepCommandContains,
      'gn check --check-generated',
      [
        'RECIPE_REPO[depot_tools]/gn.py',
        'check',
        '[CACHE]/builder/src/out/Release',
        '--check-generated',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'non-fatal-failure',
    api.step_data('gn check --check-generated (non-fatal)', retcode=1),
    # The step is still surfaced as a failure, but the recipe keeps going.
    api.post_process(StepFailure, 'gn check --check-generated (non-fatal)'),
    api.post_process(MustRun, 'after gn check'),
    api.post_process(StatusSuccess),
    api.post_process(DropExpectation),
  )
