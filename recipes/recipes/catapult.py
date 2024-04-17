# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
  'chromium',
  'depot_tools/bot_update',
  'depot_tools/gclient',
  'depot_tools/gitiles',
  'depot_tools/osx_sdk',
  'gae_sdk',
  'recipe_engine/cipd',
  'recipe_engine/context',
  'recipe_engine/generator_script',
  'recipe_engine/path',
  'recipe_engine/platform',
  'recipe_engine/properties',
]

from PB.recipes.build.catapult import InputProperties

PROPERTIES = InputProperties


def _CheckoutSteps(api):
  """Checks out the catapult repo (and any dependencies) using gclient."""
  api.gclient.set_config('catapult')
  api.bot_update.ensure_checkout()
  api.gclient.runhooks()


def _RemoteSteps(api, app_engine_sdk_path, properties):
  """Runs the build steps specified in catapult_build/build_steps.py.

  Steps are specified in catapult repo in order to avoid multi-sided patches
  when updating tests and adding/moving directories.

  This step uses the generator_script; see documentation at
  github.com/luci/recipes-py/blob/main/recipe_modules/generator_script/api.py

  Use the test_checkout_path property in local tests to run against a local
  copy of catapult_build/build_steps.py.
  """
  base = api.properties.get('test_checkout_path', str(api.path.checkout_dir))
  script = api.path.join(base, 'catapult_build', 'build_steps.py')
  platform = properties.platform
  dashboard_only = properties.dashboard_only
  perf_issue_service_only = properties.perf_issue_service_only
  args = [
      script,
      '--api-path-checkout',
      api.path.checkout_dir,
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


def RunSteps(api, properties):
  _CheckoutSteps(api)

  # The dashboard unit tests depend on Python modules in the App Engine SDK,
  # and the unit test runner script assumes that the SDK is in PYTHONPATH.
  sdk_path = api.path.start_dir.join('google_appengine')
  api.gae_sdk.fetch(api.gae_sdk.PLAT_PYTHON, sdk_path)
  app_engine_sdk_path = api.path.pathsep.join([
      '%(PYTHONPATH)s', str(sdk_path)])

  packages_root = api.path.start_dir.join('packages')

  # Install the protoc package.
  if (api.platform.name == 'mac' and api.platform.arch == 'arm'):
    ensure_file = api.cipd.EnsureFile().add_package(
        'infra/3pp/tools/protoc/${platform}', 'version:2@21.1')
  else:
    ensure_file = api.cipd.EnsureFile().add_package(
        'infra/tools/protoc/${platform}', 'protobuf_version:v3.6.1')
  api.cipd.ensure(packages_root, ensure_file)

  with api.osx_sdk('mac'):
    with api.context(
        env_prefixes={'PATH': [packages_root,
                               packages_root.join('bin')]}):
      _RemoteSteps(api, app_engine_sdk_path, properties)


def GenTests(api):
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
