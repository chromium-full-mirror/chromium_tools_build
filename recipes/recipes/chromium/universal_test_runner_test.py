# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests the universal test runner recipe

Checks out the chromium/src and tools/build, applying gerrit patches to either
and then invokes the UTR against the checked out build repo. This will allow
this recipe to test both UTR cli and recipe changes. The tests invoked are
configurable as input properties."""

import re

from contextlib import contextmanager

from recipe_engine import post_process
from recipe_engine.config_types import Path
from recipe_engine.recipe_api import RecipeApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.go.chromium.org.luci.buildbucket.proto import common
from PB.recipe_engine.result import RawResult
from PB.recipes.build.chromium.universal_test_runner_test import InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
    chromium,
    chromium_checkout,
    chromium_tests,
    chromium_tests_builder_config,
)
from RECIPE_MODULES.depot_tools import (
    bot_update,
    gclient,
    git,
    tryserver,
)
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    file,
    futures,
    json,
    path,
    platform,
    properties,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  chromium_tests: chromium_tests.API
  chromium_tests_builder_config: chromium_tests_builder_config.API
  context: context.API
  file: file.API
  futures: futures.API
  gclient: gclient.API
  git: git.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  chromium_checkout: chromium_checkout.TEST_API
  chromium_tests: chromium_tests.TEST_API
  chromium_tests_builder_config: chromium_tests_builder_config.TEST_API
  file: file.TEST_API
  gclient: gclient.TEST_API
  json: json.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  step: step.TEST_API
  tryserver: tryserver.TEST_API

PROPERTIES = InputProperties

RECIPES = [
    'chromium/universal_test_runner', 'chromium/universal_test_runner_test'
]


def RunSteps(api: RecipeApi, properties: InputProperties):
  recipe_dir, infra_dir = checkout(api)
  # If we're testing a recipe change, check to see if UTR recipe is affected
  if (api.tryserver.gerrit_change and
      api.tryserver.gerrit_change.project == 'chromium/tools/build'):
    with api.context(cwd=recipe_dir):
      affected_files = api.tryserver.get_files_affected_by_patch(recipe_dir)
    is_affected = _is_affected(
        api,
        recipe_dir,
        affected_files,
    )
    if not is_affected:
      api.step.empty('UTR is unaffected by the change')
      return

  # TODO(crbug.com/346263533): Remove this link-replacing when bundling can
  # support symlinks on windows.
  with replace_bootstrap_proto_link(api, infra_dir):
    bundle_dir = create_recipe_bundle(api, recipe_dir, infra_dir)

  # Opt-in to telemetry to verify it works
  api.step(
      'opt-in telemetry',
      cmd=[
          'vpython3',
          api.chromium_checkout.source_dir.joinpath('third_party',
                                                    'depot_tools', 'infra_lib',
                                                    'telemetry'),
          '--bot-enable',
      ])

  failed_invocations = 0
  for builder_suites in properties.builder_suites:
    test_names = ', '.join(builder_suites.test_names)
    step_name = (
        f'{builder_suites.bucket}:{builder_suites.builder_name} - {test_names}')
    cmd = [
        'vpython3',
        api.chromium_checkout.source_dir.joinpath('tools', 'utr', 'run.py'),
        '--bucket',
        builder_suites.bucket,
        '--builder',
        builder_suites.builder_name,
        '--recipe-path',
        bundle_dir,
        '--force',
        '-vv',
    ]
    if builder_suites.build_dir:
      cmd.extend([
          '--build-dir',
          api.chromium_checkout.source_dir / builder_suites.build_dir,
      ])
    for test_name in builder_suites.test_names:
      cmd.extend(['--test', test_name])
    cmd.append('compile-and-test')
    step_result = api.step(step_name, cmd, raise_on_failure=False)
    if step_result.retcode:
      failed_invocations += 1
  if failed_invocations:
    return RawResult(
        status=common.FAILURE,
        summary_markdown=f'{failed_invocations} total failed UTR runs')


# The recipes are considered affected by any file in the directory of any recipe
# module that the recipe transitively depends on. Some of the files are not part
# of the recipe code or only contain static config that doesn't impact the
# recipes being tested, so don't put them in the input to recipes.py analyze. An
# affected file will be ignored if the path relative to the root of the repo
# full matches against any of these regexes.
_FILES_TO_IGNORE_REGEXES = [
    re.compile(p) for p in (
        # The universal test runner requires the configs to be src-side, so they
        # won't be impacted by changes to recipe-side specs
        r'recipes/recipe_modules/chromium_tests_builder_config/builders/.*\.py',
        r'recipes/recipe_modules/chromium_tests_builder_config/trybots\.py',

        # recipe_modules tests and examples are not part of the production code
        r'recipe_modules/[^/]+/examples/.+',
        r'recipe_modules/[^/]+/tests/.+',

        # OWNERS files contain repository metadata and are not part of the
        # recipes
        r'(.+/)*[A-Z_]*OWNERS',

        # PRESUBMIT.py scripts are executed by the presubmit builder, not
        # consumed by chromium recipes
        r'(.+/)*PRESUBMIT.py',
    )
]


def _is_affected(
    api: DEPS,
    recipe_dir: Path,
    affected_files: list[str],
):
  """Determine whether UTR or this recipe is affected by the recipe change.

  Args:
    api - The recipe API object.
    recipe_dir - The root directory of the recipe repo.
    affected_files - The set of files affected by the change.

  Returns:
    Whether the run is affected by the change.
  """
  considered_affected_files = []
  ignored_affected_files = []
  for f in affected_files:
    f = api.path.relpath(f, recipe_dir)
    if any(r.fullmatch(f) for r in _FILES_TO_IGNORE_REGEXES):
      ignored_affected_files.append(f)
    else:
      considered_affected_files.append(f)

  if ignored_affected_files:
    step_text = None
    if not considered_affected_files:
      step_text = 'all affected files are ignored, skipping analyze'
    api.step.empty(
        'ignored affected files',
        step_text=step_text,
        log_name='files',
        log_text=ignored_affected_files,
    )
    if not considered_affected_files:
      return False

  cmd = [
      'vpython3',
      recipe_dir / 'recipes/recipes.py',
      '--package',
      recipe_dir / 'infra/config/recipes.cfg',
      'analyze',
      api.json.input({
          'files': sorted(considered_affected_files),
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


def checkout(api: DEPS):
  """Checks out chromium/src and build repos.

  Returns tuple of (path to the tools/build.git recipe checkout, path to the
    infra/infra.git checkout). The src checkout can be accessed at
    `api.chromium_checkout.source_dir`.
  """
  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  api.chromium_tests.configure_build(builder_config)

  # Add the infra superproject to get the build repo
  s = api.gclient.c.solutions.add()
  s.url = 'https://chromium.googlesource.com/infra/infra_superproject.git'
  s.name = 'infra'

  api.chromium_tests.check_builder_cache(
      api.chromium_checkout.default_checkout_dir)

  update_result = api.chromium_checkout.ensure_checkout()
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)
  api.chromium.runhooks(source_dir, build_dir)

  infra_source_dir = update_result.checkout_dir / 'infra'
  return infra_source_dir / 'build', infra_source_dir / 'infra'


@contextmanager
def replace_bootstrap_proto_link(api: DEPS, infra_dir: Path):
  """Replaces a known-symlink in a recipe checkout with a copy of its target.

  Creating a bundle on windows breaks due to this symlink:
  https://source.chromium.org/chromium/infra/infra_superproject/+/main:infra/recipes/recipe_proto/infra/chromium/chromium_bootstrap.proto;drc=d2c49f7afb1480f25d54c481d4444d796f915ebf

  So this method will temporarily replace that link with a copy of what it
  points to.

  TODO(crbug.com/346263533): Can delete this method after bundling is fixed.
  """
  if api.platform.name != 'win':
    yield
    return

  bootstrap_proto_link = infra_dir.joinpath('recipes', 'recipe_proto', 'infra',
                                            'chromium',
                                            'chromium_bootstrap.proto')
  bootstrap_proto_target = infra_dir.joinpath('go', 'src', 'infra', 'chromium',
                                              'bootstrapper', 'bootstrap',
                                              'chromium_bootstrap.proto')
  tmp_bootstrap_proto_link = api.path.mkdtemp().joinpath(
      'chromium_bootstrap.proto')

  api.file.copy('store proto link', bootstrap_proto_link,
                tmp_bootstrap_proto_link)
  api.file.copy('replace proto link', bootstrap_proto_target,
                bootstrap_proto_link)
  yield
  api.file.copy('restore proto link', tmp_bootstrap_proto_link,
                bootstrap_proto_link)


def create_recipe_bundle(api: DEPS, recipe_dir: Path, infra_dir: Path):
  """Creates a hermetic recipe bundle via `recipes.py bundle`."""
  bundle_dir = api.path.mkdtemp('recipe_bundle')
  api.step(
      'create bundle',
      [
          'vpython3',
          recipe_dir / 'recipes' / 'recipes.py',
          # Override the default infra checkout during bundling. By default it will
          # fetch a duplicate copy from git. But we want to use our fixed-up
          # version.
          # TODO(crbug.com/346263533): Can remove this line once bundling works on
          # windows.
          '-O',
          'infra=' + str(infra_dir),
          'bundle',
          '--destination',
          bundle_dir,
      ])
  return bundle_dir


def GenTests(api: RecipeTestApi):
  ctbc_api = api.chromium_tests_builder_config

  def gen_test_props():
    t = api.chromium.ci_build(
        builder_group='fake-group',
        builder='fake-builder',
    )
    return t + ctbc_api.properties(
        ctbc_api.properties_assembler_for_ci_builder(
            builder_group='fake-group',
            builder='fake-builder',
        ).assemble())

  default_builder_suites = [
      {
          'bucket': 'fake-bucket',
          'builder_name': 'fake-builder',
          'test_names': ['testA', 'testB'],
          'build_dir': 'fake/build',
      },
      {
          'bucket': 'fake-bucket',
          'builder_name': 'fake-builder2',
          'test_names': ['testZ'],
          'build_dir': 'fake/build',
      },
  ]
  yield api.test(
      'basic',
      gen_test_props(),
      api.properties(builder_suites=default_builder_suites),
      api.post_process(post_process.StepCommandContains,
                       'fake-bucket:fake-builder - testA, testB', [
                         '[CACHE]/builder/src/tools/utr/run.py', \
                         '--bucket', 'fake-bucket',
                         '--builder', 'fake-builder',
                         '--recipe-path', '[CLEANUP]/recipe_bundle_tmp_1',
                         '--force',
                         '-vv',
                         '--build-dir', '[CACHE]/builder/src/fake/build',
                         '--test', 'testA',
                         '--test', 'testB',
                         'compile-and-test',
                         ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'win',
      gen_test_props(),
      api.platform.name('win'),
      api.properties(builder_suites=default_builder_suites),
      api.post_process(post_process.MustRun, 'store proto link'),
      api.post_process(post_process.MustRun, 'restore proto link'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'build_repo_change',
      gen_test_props(),
      api.properties(builder_suites=default_builder_suites),
      api.buildbucket.try_build(project='chromium/tools/build'),
      api.step_data(
          'determine affected recipes',
          api.json.output({
              'recipes': [],
              'error': '',
              'invalid_recipes': [],
          }),
          retcode=0),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'all-files-ignored',
      gen_test_props(),
      api.properties(builder_suites=default_builder_suites),
      api.buildbucket.try_build(project='chromium/tools/build'),
      api.tryserver.get_files_affected_by_patch([
          'recipes/recipe_modules/chromium_tests_builder_config/builders/x.py',
          'recipes/recipe_modules/chromium_tests_builder_config/trybots.py',
          'recipe_modules/foo/examples/bar.py',
          'recipe_modules/foo/tests/bar.py',
          'OWNERS',
          'foo/BAR_OWNERS',
          'PRESUBMIT.py',
          'foo/PRESUBMIT.py',
      ]),
      api.post_check(
          post_process.StepTextEquals,
          'ignored affected files',
          'all affected files are ignored, skipping analyze',
      ),
      api.post_check(post_process.MustRun, 'UTR is unaffected by the change'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'utr_fails',
      gen_test_props(),
      api.properties(builder_suites=default_builder_suites),
      api.step_data('fake-bucket:fake-builder - testA, testB', retcode=1),
      api.step_data('fake-bucket:fake-builder2 - testZ', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.SummaryMarkdown, '2 total failed UTR runs'),
      api.post_process(post_process.DropExpectation),
  )
