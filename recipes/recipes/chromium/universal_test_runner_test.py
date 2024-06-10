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
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/futures',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
]

PROPERTIES = InputProperties

RECIPES = [
    'chromium/universal_test_runner', 'chromium/universal_test_runner_test'
]


def RunSteps(api: RecipeApi, properties: InputProperties):
  recipe_dir = checkout(api)
  # If we're testing a recipe change, check to see if UTR recipe is affected
  if (api.tryserver.gerrit_change and
      api.tryserver.gerrit_change.project == 'chromium/tools/build'):
    with api.context(cwd=recipe_dir):
      affected_files = api.tryserver.get_files_affected_by_patch(recipe_dir)
    is_affected = _is_affected(
        api,
        affected_files,
        recipe_dir / 'recipes' / 'recipes.py',
        recipe_dir / 'infra' / 'config' / 'recipes.cfg',
    )
    if not is_affected:
      api.step.empty('UTR is unaffected by the change')
      return
  bundle_dir = create_recipe_bundle(api, recipe_dir)
  for builder_suites in properties.builder_suites:
    test_names = ', '.join(builder_suites.test_names)
    step_name = (
        f'{builder_suites.bucket}:{builder_suites.builder_name} - {test_names}')
    build_dir = api.chromium_checkout.source_dir / builder_suites.build_dir
    cmd = [
        'vpython3',
        api.chromium_checkout.source_dir.joinpath('tools', 'utr', 'run.py'),
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


def _is_affected(
    api: RecipeApi,
    affected_files: list[str],
    recipes_py_path: Path,
    recipes_cfg_path: Path,
):
  """Determine whether UTR or this recipe is affected by the recipe change.

  Args:
    api - The recipe API object.
    affected_files - The set of files affected by the change.
    recipes_py_path - A Path object identifying the location of the
      recipes.py script.
    recipes_cfg_path - A Path object identifying the location of the
      recipes.cfg file.

  Returns:
    Whether the run is affected by the change.
  """
  cmd = [
      'vpython3',
      recipes_py_path,
      '--package',
      recipes_cfg_path,
      'analyze',
      api.json.input({
          'files': sorted(affected_files),
          'recipes': sorted(RECIPES),
      }),
      api.json.output(),
  ]

  step_name = 'determine affected recipes'
  result = api.step(
      step_name,
      cmd,
      step_test_data=lambda: api.json.test_api.output({'recipes': []}),
  )

  affected_recipes = result.json.output['recipes']
  result.presentation.logs['recipes'] = '\n'.join(affected_recipes)

  return affected_recipes


def checkout(api: RecipeApi):
  """Checks out chromium/src and build repos.

  Returns path to the tools/build.git recipe checkout. The src checkout can
  be accessed at `api.chromium_checkout.source_dir`.
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
  default_builder_suites = [{
      'bucket': 'fake-bucket',
      'builder_name': 'fake-builder',
      'test_names': ['testA', 'testB'],
      'build_dir': 'fake/build',
  }]
  yield api.test(
      'basic',
      api.properties(builder_suites=default_builder_suites),
      api.post_process(post_process.StepCommandContains,
                       'fake-bucket:fake-builder - testA, testB', [
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
      'build_repo_change',
      api.properties(builder_suites=default_builder_suites),
      api.buildbucket.try_build(project='chromium/tools/build'),
      api.step_data(
          'determine affected recipes',
          api.json.output({
              'recipes': '',
              'error': '',
              'invalid_recipes': [],
          }),
          retcode=0),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'utr_fails',
      api.properties(builder_suites=default_builder_suites),
      api.step_data('fake-bucket:fake-builder - testA, testB', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
