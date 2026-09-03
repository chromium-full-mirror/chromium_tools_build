# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Runs Crowbar workflows in chromium/src repos.

Checks out chromium/src, then find and trigger Crowbar workflows to vendor
third-party code into chromium/src, or to build and publish third-party code
as CIPD packages.

For more information, see:
https://chromium.googlesource.com/chromium/src/+/main/docs/managing-third-party/crowbar-workflow.md
"""

from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto import build as build_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_checkout
from RECIPE_MODULES.depot_tools import gclient
from RECIPE_MODULES.infra import crowbar
from RECIPE_MODULES.recipe_engine import properties, raw_io, step


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  crowbar: crowbar.API
  gclient: gclient.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  chromium: chromium.TEST_API
  raw_io: raw_io.TEST_API
  step: step.TEST_API

# Path to git repos (including submodules) to find Crowbar packages.
_REPO_PATHS = [
    'src',
]


def RunSteps(api: DEPS):
  api.gclient.set_config('chromium')

  c = api.chromium_checkout.ensure_checkout()
  for p in _REPO_PATHS:
    repo_dir = c.checkout_dir / p
    pkgs = api.crowbar.list_packages(repo_dir)
    api.crowbar.build(repo_dir, packages=pkgs)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'ci build',
      api.chromium.ci_build(
          builder_group='chromium.linux',
          builder='Linux Builder',
      ),
      api.step_data(
          'Find Crowbar Packages',
          api.raw_io.stream_output_text('foo/crowbar.txtpb\n', stream='stdout'),
      ),
      api.step_data(
          'build packages',
          api.step.sub_build(build_pb2.Build(status=common_pb.SUCCESS)),
      ),
      api.post_process(post_process.StepCommandContains, 'build packages',
                       ['foo']),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
