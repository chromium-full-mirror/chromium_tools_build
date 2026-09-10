# Copyright 2025 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for dawn.get_go_paths()."""

from __future__ import annotations

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import dawn
from RECIPE_MODULES.recipe_engine import path, platform, step


@dataclass
class DEPS(RecipeScriptApi):
  dawn: dawn.API
  path: path.API
  platform: platform.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  platform: platform.TEST_API


def RunSteps(api: DEPS):
  go_paths = api.dawn.get_go_paths(api.path.start_dir)
  for i, p in enumerate(go_paths):
    api.step.empty('Path %d' % i, step_text=str(p))


def GenTests(api: TEST_DEPS):
  yield api.test(
    'linux-amd64',
    api.platform('linux', 64, 'intel'),
    api.post_check(post_process.MustRun, 'Path 0'),
    api.post_check(
      post_process.StepTextContains, 'Path 0', ['tools/golang/linux-amd64/bin']
    ),
    api.post_check(post_process.MustRun, 'Path 1'),
    api.post_check(
      post_process.StepTextContains, 'Path 1', ['tools/golang/bin']
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'linux-arm64',
    api.platform('linux', 64, 'arm'),
    api.post_check(post_process.MustRun, 'Path 0'),
    api.post_check(
      post_process.StepTextContains, 'Path 0', ['tools/golang/linux-arm64/bin']
    ),
    api.post_check(post_process.MustRun, 'Path 1'),
    api.post_check(
      post_process.StepTextContains, 'Path 1', ['tools/golang/bin']
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'mac-amd64',
    api.platform('mac', 64, 'intel'),
    api.post_check(post_process.MustRun, 'Path 0'),
    api.post_check(
      post_process.StepTextContains, 'Path 0', ['tools/golang/mac-amd64/bin']
    ),
    api.post_check(post_process.MustRun, 'Path 1'),
    api.post_check(
      post_process.StepTextContains, 'Path 1', ['tools/golang/bin']
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'mac-arm64',
    api.platform('mac', 64, 'arm'),
    api.post_check(post_process.MustRun, 'Path 0'),
    api.post_check(
      post_process.StepTextContains, 'Path 0', ['tools/golang/mac-arm64/bin']
    ),
    api.post_check(post_process.MustRun, 'Path 1'),
    api.post_check(
      post_process.StepTextContains, 'Path 1', ['tools/golang/bin']
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'windows-amd64',
    api.platform('win', 64, 'intel'),
    api.post_check(post_process.MustRun, 'Path 0'),
    api.post_check(
      post_process.StepTextContains,
      'Path 0',
      ['tools\\golang\\windows-amd64\\bin'],
    ),
    api.post_check(post_process.MustRun, 'Path 1'),
    api.post_check(
      post_process.StepTextContains, 'Path 1', ['tools\\golang\\bin']
    ),
    api.post_process(post_process.DropExpectation),
  )
  yield api.test(
    'windows-arm64',
    api.platform('win', 64, 'arm'),
    api.post_check(post_process.MustRun, 'Path 0'),
    api.post_check(
      post_process.StepTextContains,
      'Path 0',
      ['tools\\golang\\windows-arm64\\bin'],
    ),
    api.post_check(post_process.MustRun, 'Path 1'),
    api.post_check(
      post_process.StepTextContains, 'Path 1', ['tools\\golang\\bin']
    ),
    api.post_process(post_process.DropExpectation),
  )
