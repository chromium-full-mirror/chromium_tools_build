# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
'''
Recipe for running Crossbench's End2End tests on Android.
'''

from contextlib import contextmanager
from PB.recipes.build.perf.crossbench_android import InputProperties

DEPS = [
    'adb',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'recipe_engine/buildbucket',
    'recipe_engine/cas',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/platform',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]

PROPERTIES = InputProperties
_AVD_CIPD_VERSION = 'latest'
# A list of available AVDs: docs/android_emulator.md
_AVD_CONFIG_VERSION = 'android_%s_google_apis_x64%s.textpb'
# The prefix is used in the tests to identify whether they are running in CQ.
_CAS_DIR_PREFIX = 'cq_archive_'


class AndroidEmulator:

  def __init__(self, api, android_sdk, avd_suffix=''):
    self.api = api
    self.android_sdk = android_sdk
    self.avd_suffix = avd_suffix
    self.avd_root = None
    self.avd_script = None
    self.avd_config_version = None
    self.avd_config = None
    self.adb_path = None
    self.adb_device_id = None
    self._initialized = False

  @contextmanager
  def start(self):
    assert self.api.platform.is_linux
    self._initialize()
    try:
      self._install_and_start()
      yield
    finally:
      self._kill()
      self._uninstall()

  def _initialize(self):
    assert not self._initialized
    self.avd_root = self.api.path.cache_dir / 'avd'
    avd_path = self.avd_root / 'src/tools/android/avd'
    self.avd_script = avd_path / 'avd.py'
    self.avd_config_version = _AVD_CONFIG_VERSION % (self.android_sdk,
                                                     self.avd_suffix)
    self.avd_config = avd_path / 'proto' / self.avd_config_version
    self._download()
    self._initialized = True

  def _download(self):
    with self.api.step.nest('Download avd package'):
      with self.api.context(), self.api.depot_tools.on_path():
        self.api.cipd.ensure(
            self.avd_root,
            self.api.cipd.EnsureFile().add_package(
                'chromium/tools/android/avd/linux-amd64', _AVD_CIPD_VERSION))
      adb_root = (
          self.avd_root / 'src/third_party/android_sdk/public/platform-tools')
      self.adb_path = adb_root / 'adb'
      assert self.api.path.exists(
          self.adb_path), (f'{self.adb_path} doesn\'t exist!')

  def _install_and_start(self):
    with self.api.context(cwd=self.avd_root):
      with self.api.step.nest('Install %s' % self.avd_config_version):
        self.api.step(
            'Install Android emulator', [
                'vpython3', self.avd_script, 'install', '--avd-config',
                self.avd_config
            ],
            stdout=self.api.raw_io.output_text(add_output_log=True))
        self._start()

  def _start(self, max_attempts=3):
    log_lines = []
    for count_attempt in range(max_attempts):
      log_lines.append(f'Attempt# {count_attempt}')
      if count_attempt > 0:
        self._kill()
      result = self.api.step(
          'Start Android emulator: %s' % count_attempt,
          [
              'xvfb-run', 'vpython3', self.avd_script, 'start', '--debug-tags',
              'all', '--gpu-mode', 'off', '--avd-config', self.avd_config,
              '--enable-network'
          ],
          stdout=self.api.raw_io.output_text(add_output_log=True),
          infra_step=True,
          raise_on_failure=False,
      )
      if result.retcode != 0:
        log_lines.append('Unable to start emulator!')
        continue
      devices = self.api.adb.list_devices(self.adb_path)
      if len(devices) != 1:
        log_lines.append(
            f'There must be exactly one active ADB device: {devices}')
        continue
      self.adb_device_id = devices[0]
      if self._setup() == 0:
        return
      log_lines.append('Unable to set up the emulator!')
    raise self.api.step.StepFailure('\n'.join(log_lines))

  def _setup(self):
    with self.api.step.nest('Set up the emulator'):
      with self.api.context(cwd=self.avd_root):
        resource_name = self.api.resource('avd_setup.sh')
        result = self.api.step(
            'Set execute permission',
            ['chmod', '755', resource_name],
            infra_step=True,
            raise_on_failure=False,
        )
        if result.retcode == 0:
          result = self.api.step(
              'avd_setup.sh',
              [resource_name, str(self.adb_path)],
              infra_step=True,
              raise_on_failure=False,
          )
    return result.retcode

  def _kill(self):
    with self.api.step.nest('Kill and cleanup avd'):
      self.api.step('List processes before cleaning up', ['ps', 'aux'])
      # Accepting any return code because when the emulator dies the pid is no longer
      # available causing an exception.
      self.api.step(
          'Kill emulator cleanup', ['pkill', '-9', '-e', '-f', 'emulator'],
          ok_ret='any')
      self.api.step('List processes after cleaning up', ['ps', 'aux'])

  def _uninstall(self):
    with self.api.context(cwd=self.avd_root):
      with self.api.step.nest('Uninstall %s' % self.avd_config):
        self.api.step(
            'Uninstall Android emulator', [
                'vpython3', self.avd_script, 'uninstall', '--avd-config',
                self.avd_config
            ],
            stdout=self.api.raw_io.output_text(add_output_log=True))


class _TestRunConfig:

  def __init__(self, sdk_version, avd_suffix, *extra_flags):
    self.sdk_version = sdk_version
    self.avd_suffix = avd_suffix
    self.extra_flags = extra_flags


def RunSteps(api, properties):
  api.gclient.set_config('crossbench')
  api.bot_update.ensure_checkout()
  api.gclient.runhooks()

  test_driver = 'crossbench/tests/end2end/android/runner.py'

  configs = [
      _TestRunConfig(properties.android_sdk, '', '-m',
                     'not legacy_android_sdk'),
      _TestRunConfig(32, '_foldable', '-m', 'legacy_android_sdk'),
  ]

  for config in configs:
    android_emulator = AndroidEmulator(
        api, config.sdk_version, avd_suffix=config.avd_suffix)
    with android_emulator.start():
      env = {}
      with api.context(env=env):
        cas_archive = api.path.mkdtemp(_CAS_DIR_PREFIX)
        try:
          api.step(
              'Run Android End2End Tests',
              [
                  'vpython3',
                  test_driver,
                  f'--adb-device-id={android_emulator.adb_device_id}',
                  f'--adb-path={android_emulator.adb_path}',
                  f'--cas-archive={cas_archive}',
                  f'--log-file={cas_archive}/pytest.tests.android.out.txt',
                  *config.extra_flags,
              ],
          )
        finally:
          api.cas.archive('Copy End2End test logs to CAS', cas_archive,
                          cas_archive)


def GenTests(api):

  _INSTALL_STEP = 'Install android_35_google_apis_x64.textpb'

  def gen_test_data_retry_start(max_attempts=2):
    start_step = 'Start Android emulator'
    data = api.empty_test_data()
    for count_attempt in range(max_attempts):
      data += api.step_data(
          f'{_INSTALL_STEP}.{start_step}: {count_attempt}', retcode=1)
    return data

  def gen_adb_path():
    return api.path.cache_dir.joinpath('avd', 'src', 'third_party',
                                       'android_sdk', 'public',
                                       'platform-tools', 'adb')

  yield api.test(
      'basic-android-test',
      api.platform('linux', 64),
      api.platform.arch('intel'),
      api.properties(android_sdk=35),
      api.path.exists(gen_adb_path()),
  )

  yield api.test(
      'retry-android-test',
      api.platform('linux', 64),
      api.platform.arch('intel'),
      api.properties(android_sdk=35),
      api.path.exists(gen_adb_path()),
      gen_test_data_retry_start(),
      api.step_data(
          f'{_INSTALL_STEP}.Set up the emulator.avd_setup.sh', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'no-emulator-test', api.platform('linux', 64), api.platform.arch('intel'),
      api.properties(android_sdk=35), api.path.exists(gen_adb_path()),
      api.step_data(f'{_INSTALL_STEP}.List adb devices', api.json.output([])))
