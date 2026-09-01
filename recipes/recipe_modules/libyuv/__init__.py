# Copyright 2014 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from RECIPE_MODULES.build import (
    archive,
    builder_group,
    chromium,
    chromium_android,
    chromium_checkout,
    chromium_tests,
)
from RECIPE_MODULES.depot_tools import (
    bot_update,
    gclient,
    osx_sdk,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    defer,
    file,
    platform,
    properties,
    runtime,
    scheduler,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  archive: archive.API
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_android: chromium_android.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  bot_update: bot_update.API
  gclient: gclient.API
  osx_sdk: osx_sdk.API
  tryserver: tryserver.API
  buildbucket: buildbucket.API
  context: context.API
  defer: defer.API
  file: file.API
  platform: platform.API
  properties: properties.API
  runtime: runtime.API
  scheduler: scheduler.API
  step: step.API


# TODO(phajdan.jr): provide coverage (http://crbug.com/693058).
DISABLE_STRICT_COVERAGE = True

from .api import LibyuvApi as API
from .test_api import LibyuvTestApi as TEST_API
