# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Generates BoringSSL documentation and uploads it to Cloud Storage."""


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.depot_tools import bot_update, gclient, gsutil
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    path,
    properties,
    runtime,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  context: context.API
  gclient: gclient.API
  gsutil: gsutil.API
  path: path.API
  properties: properties.API
  runtime: runtime.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  runtime: runtime.TEST_API


def RunSteps(api: DEPS):
  # Sync and pull in everything.
  api.gclient.set_config('boringssl')
  cache_dir = api.path.cache_dir / 'builder'
  with api.context(cwd=cache_dir):
    api.bot_update.ensure_checkout()
    api.gclient.runhooks()

  # Set up paths.
  util = cache_dir.joinpath('boringssl', 'util')
  goroot = util / 'bot' / 'golang'
  output = api.path.mkdtemp('boringssl-docs')

  # Generate and upload documentation.
  with api.context(
      cwd=util, env={'GOROOT': goroot},
      env_prefixes={'PATH': [goroot / 'bin']}):
    api.step('generate', ['go', 'run', 'doc.go', '-out', output])
  # Upload docs only if run after commit and on not experimental builds.
  if api.buildbucket.build.builder.bucket == 'ci':
    if api.runtime.is_experimental:
      api.step('skipping uploading docs on experimental build', cmd=None)
    else:
      api.gsutil(['-m', 'cp', '-a', 'public-read', api.path.join(output, '**'),
                  'gs://chromium-boringssl-docs/'])


def GenTests(api: TEST_DEPS):
  yield api.test(
      'docs',
      api.buildbucket.ci_build(
          project='boringssl',
          bucket='ci',
          builder='docs',
          git_repo='https://boringssl.googlesource.com/boringssl',
      ),
  )
  yield api.test(
      'docs-experimental',
      api.runtime(is_experimental=True),
      api.buildbucket.ci_build(
          project='boringssl',
          bucket='ci',
          builder='docs',
          git_repo='https://boringssl.googlesource.com/boringssl',
      ),
  )

  yield api.test(
      'docs-try',
      api.buildbucket.try_build(
          project='boringssl',
          bucket='try',
          builder='docs',
          git_repo='https://boringssl.googlesource.com/boringssl',
      ),
  )
