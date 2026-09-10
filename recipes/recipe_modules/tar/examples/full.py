# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import tar
from RECIPE_MODULES.recipe_engine import (
  context,
  file,
  path,
  platform,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  context: context.API
  file: file.API
  path: path.API
  platform: platform.API
  step: step.API
  tar: tar.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  platform: platform.TEST_API


def RunSteps(api: DEPS):
  # Prepare files.
  temp = api.path.mkdtemp('tar-example')
  api.step('touch a', ['touch', temp / 'a'])
  api.step('touch b', ['touch', temp / 'b'])
  api.file.ensure_directory('mkdirs', temp.joinpath('sub', 'dir'))
  api.step('touch c', ['touch', temp.joinpath('sub', 'dir', 'c')])

  # Build tar using 'tar.directory'.
  api.tar.directory('taring', temp, temp / 'output.tar')

  # Build a tar using TarPackage api.
  package = api.tar.make_package(temp, temp / 'more.tar.gz', 'gz')
  package.add_file(package.root / 'a')
  package.add_file(package.root / 'b')
  package.add_directory(package.root / 'sub')
  package.tar('taring more')

  # Coverage for 'output' property.
  api.step('report', ['echo', package.output])

  # Untar the package.
  api.tar.untar(
    'untaring', temp.joinpath('output.tar'), temp.joinpath('output'), quiet=True
  )
  # List untarped content.
  with api.context(cwd=temp / 'output'):
    api.step('listing', ['find'])
  # Clean up.
  api.file.rmtree('cleanup', temp)


def GenTests(api: TEST_DEPS):
  for platform in ('linux', 'win', 'mac'):
    yield api.test(
      platform,
      api.platform.name(platform),
    )
