# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, gae_sdk
from RECIPE_MODULES.depot_tools import (
    bot_update,
    gclient,
    gitiles,
    osx_sdk,
)
from RECIPE_MODULES.recipe_engine import (
    cipd,
    context,
    generator_script,
    path,
    platform,
    properties,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  chromium: chromium.API
  cipd: cipd.API
  context: context.API
  gae_sdk: gae_sdk.API
  gclient: gclient.API
  generator_script: generator_script.API
  gitiles: gitiles.API
  osx_sdk: osx_sdk.API
  path: path.API
  platform: platform.API
  properties: properties.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  gclient: gclient.TEST_API
  generator_script: generator_script.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API

from PB.recipes.build.catapult import InputProperties

PROPERTIES = InputProperties


def _CheckoutSteps(api: DEPS):
  """Checks out the catapult repo (and any dependencies) using gclient."""
  api.gclient.set_config('catapult')
  update_result = api.bot_update.ensure_checkout()
  api.gclient.runhooks()
  return update_result


def _RemoteSteps(api: DEPS, source_dir, app_engine_sdk_path, properties):
  """Runs the build steps specified in catapult_build/build_steps.py.

  Steps are specified in catapult repo in order to avoid multi-sided patches
  when updating tests and adding/moving directories.

  This step uses the generator_script; see documentation at
  github.com/luci/recipes-py/blob/main/recipe_modules/generator_script/api.py

  Use the test_checkout_path property in local tests to run against a local
  copy of catapult_build/build_steps.py.
  """
  base = api.properties.get('test_checkout_path', str(source_dir))
  script = api.path.join(base, 'catapult_build', 'build_steps.py')
  platform = properties.platform
  dashboard_only = properties.dashboard_only
  perf_issue_service_only = properties.perf_issue_service_only
  args = [
      script,
      '--api-path-checkout',
      source_dir,
      '--app-engine-sdk-pythonpath',
      app_engine_sdk_path,
      '--platform',
      platform or api.platform.name,
      '--platform_arch',
      api.platform.arch,
  ]
  if dashboard_only:
    args.append('--dashboard_only')
  elif perf_issue_service_only:
    args.append('--perf_issue_service_only')
  return api.generator_script(*args, interpreter='vpython3')


def RunSteps(api: DEPS, properties):
  update_result = _CheckoutSteps(api)

  # The dashboard unit tests depend on Python modules in the App Engine SDK,
  # and the unit test runner script assumes that the SDK is in PYTHONPATH.
  sdk_path = api.path.start_dir / 'google_appengine'
  api.gae_sdk.fetch(api.gae_sdk.PLAT_PYTHON, sdk_path)
  app_engine_sdk_path = api.path.pathsep.join([
      '%(PYTHONPATH)s', str(sdk_path)])

  packages_root = api.path.start_dir / 'packages'

  # Install the protoc package.
  if (api.platform.name == 'mac' and api.platform.arch == 'arm'):
    ensure_file = api.cipd.EnsureFile().add_package(
        'infra/3pp/tools/protoc/${platform}', 'version:2@21.1')
  else:
    ensure_file = api.cipd.EnsureFile().add_package(
        'infra/tools/protoc/${platform}', 'protobuf_version:v3.6.1')
  api.cipd.ensure(packages_root, ensure_file)

  source_dir = update_result.source_root.path
  with api.osx_sdk('mac'):
    with api.context(
        env_prefixes={'PATH': [packages_root, packages_root / 'bin']}):
      _RemoteSteps(api, source_dir, app_engine_sdk_path, properties)


def GenTests(api: TEST_DEPS):
  yield api.test(
      'basic',
      api.platform.name('win'),
      api.generator_script(
          'build_steps.py',
          {
              'name': 'Dashboard Tests',
              'cmd': ['run_py_tests', '--no-hooks']
          },
      ),
  )

  yield api.test(
      'mac',
      api.platform.name('mac'),
      api.generator_script(
          'build_steps.py',
          {
              'name': 'Dashboard Tests',
              'cmd': ['run_py_tests', '--no-hooks']
          },
      ),
  )

  yield api.test(
    'mac-arm',
    api.platform.name('mac'),
    api.platform.arch('arm'),
    api.generator_script(
          'build_steps.py',
          {
              'name': 'Dashboard Tests',
              'cmd': ['run_py_tests', '--no-hooks']
          },
    )
  )

  yield api.test(
      'android',
      api.properties(
          platform='android'),
      api.generator_script(
          'build_steps.py',
          {
              'name': 'Dashboard Tests',
              'cmd': ['run_py_tests', '--no-hooks']
          },
      ),
  )

  yield api.test(
      'dashboard_only', api.platform.name('linux'),
      api.properties(
          platform='linux',
          dashboard_only=True,
      ),
      api.generator_script(
          'build_steps.py',
          {
              'name': 'Dashboard Tests',
              'cmd': ['run_py_tests', '--no-hooks']
          },
      ))

  yield api.test(
      'perf_issue_service_only', api.platform.name('linux'),
      api.properties(
          platform='linux',
          perf_issue_service_only=True,
      ),
      api.generator_script(
          'build_steps.py',
          {
              'name': 'Dashboard Tests',
              'cmd': ['run_py_tests', '--no-hooks']
          },
      ))
