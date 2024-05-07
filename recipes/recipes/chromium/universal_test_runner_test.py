# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests the universal test runner recipe

Checks out the chromium/src and tools/build, applying gerrit patches to either
and then invokes the UTR against the checked out build repo. This will allow
this recipe to test both UTR cli and recipe changes. The tests invoked are
configurable as input properties."""

from recipe_engine import post_process
from recipe_engine.config_types import Path
from recipe_engine.recipe_api import RecipeApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.recipes.build.chromium.universal_test_runner_test import InputProperties

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/bot_update',
    'recipe_engine/context',
    'recipe_engine/futures',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
]

PROPERTIES = InputProperties


def RunSteps(api: RecipeApi, properties: InputProperties):
  recipe_dir = checkout(api)
  bundle_dir = create_recipe_bundle(api, recipe_dir)
  for builder_suites in properties.builder_suites:
    step_name = f'{builder_suites.bucket}:{builder_suites.builder_name}'
    build_dir = api.chromium_checkout.src_dir / builder_suites.build_dir
    cmd = [
        'vpython3',
        api.chromium_checkout.src_dir.joinpath('tools', 'utr', 'run.py'),
        '--bucket',
        builder_suites.bucket,
        '--builder',
        builder_suites.builder_name,
        '--build-dir',
        build_dir,
        '--recipe-path',
        bundle_dir,
        '--force',
        '-vv',
    ]
    for test_name in builder_suites.test_names:
      cmd.extend(['--test', test_name])
    cmd.append('compile-and-test')
    api.step(step_name, cmd)


def checkout(api: RecipeApi):
  """Checks out chromium/src and build repos.

  Returns path to the tools/build.git recipe checkout. The src checkout can
  be accessed at `api.chromium_checkout.src_dir`.
  """
  api.gclient.set_config('chromium')
  api.chromium.set_config('chromium')

  # Add the infra superproject to get the build repo
  s = api.gclient.c.solutions.add()
  s.url = 'https://chromium.googlesource.com/infra/infra_superproject.git'
  s.name = 'infra'

  api.chromium_checkout.ensure_checkout()
  api.chromium.runhooks()
  return api.path.cache_dir / 'builder' / 'infra' / 'build'


def create_recipe_bundle(api: RecipeApi, recipe_dir: Path):
  """Creates a hermetic recipe bundle via `recipes.py bundle`."""
  bundle_dir = api.path.mkdtemp('recipe_bundle')
  api.step('create bundle', [
      recipe_dir / 'recipes' / 'recipes.py',
      'bundle',
      '--destination',
      bundle_dir,
  ])
  return bundle_dir


def GenTests(api: RecipeTestApi):
  yield api.test(
      'basic',
      api.properties(builder_suites=[
        {
          'bucket': 'fake-bucket',
          'builder_name': 'fake-builder',
          'test_names': [
            'testA',
            'testB'
          ],
          'build_dir': 'fake/build',
        }
        ]),
      api.post_process(post_process.StepCommandContains,
                       'fake-bucket:fake-builder', [
                         '[CACHE]/builder/src/tools/utr/run.py', \
                         '--bucket', 'fake-bucket',
                         '--builder', 'fake-builder',
                         '--build-dir', '[CACHE]/builder/src/fake/build',
                         '--recipe-path', '[CLEANUP]/recipe_bundle_tmp_1',
                         '--force',
                         '-vv',
                         '--test', 'testA',
                         '--test', 'testB',
                         'compile-and-test',
                         ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'utr_fails',
      api.properties(builder_suites=[{
          'bucket': 'fake-bucket',
          'builder_name': 'fake-builder',
          'test_names': ['testA', 'testB'],
          'build_dir': 'fake/build',
      }]),
      api.step_data('fake-bucket:fake-builder', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
