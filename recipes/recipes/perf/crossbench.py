# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
'''
Recipe for running Crossbench's End2End tests.
'''

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'recipe_engine/file',
    'recipe_engine/platform',
    'recipe_engine/path',
    'recipe_engine/step',
]


def RunSteps(api):
  api.gclient.set_config('crossbench')
  api.bot_update.ensure_checkout()
  api.gclient.runhooks()

  chrome_app_path, chrome_driver_path = download_chrome(api)

  test_driver = 'crossbench/tests/end2end/runner.py'
  api.step('Run End2End Tests', [
      'vpython3', test_driver,
      '--test-browser-path=%s' % chrome_app_path,
      '--test-driver-path=%s' % chrome_driver_path
  ])


def GenTests(api):
  yield api.test('basic')
  yield api.test(
      'win-intel',
      api.platform('win', 64),
      api.platform.arch('intel'),
      api.expect_exception('NotImplementedError'),
  )
  yield api.test(
      'linux-intel-64',
      api.platform('linux', 64),
      api.platform.arch('intel'),
  )
  yield api.test(
      'linux-intel-32',
      api.platform('linux', 32),
      api.platform.arch('intel'),
      api.expect_exception('NotImplementedError'),
  )
  yield api.test(
      'mac-intel-64',
      api.platform('mac', 64),
      api.platform.arch('intel'),
  )
  yield api.test(
      'mac-arm-64',
      api.platform('mac', 64),
      api.platform.arch('arm'),
  )


CHROME_CONFIG = {
    ('mac', 'arm', 64): {
        'gs_path': 'Mac_Arm',
        'chrome_archive_stem': 'chrome-mac',
        'driver_archive_stem': 'chromedriver_mac64',
        'app_name': 'Chromium.app'
    },
    ('mac', 'intel', 64): {
        'gs_path': 'Mac',
        'chrome_archive_stem': 'chrome-mac',
        'driver_archive_stem': 'chromedriver_mac64',
        'app_name': 'Chromium.app'
    },
    ('linux', 'intel', 64): {
        'gs_path': 'Linux_x64',
        'chrome_archive_stem': 'chrome-linux',
        'driver_archive_stem': 'chromedriver_linux64',
        'app_name': 'chrome'
    },
}


def download_chrome(api):
  """
  Download the latest chrome binaries
  """
  with api.step.nest('Download Chrome Binaries'):
    chrome = api.path.mkdtemp(prefix='chrome')
    gs_bucket = 'chromium-browser-snapshots'
    version_file = 'LAST_CHANGE'

    key = (api.platform.name, api.platform.arch, api.platform.bits)
    if key not in CHROME_CONFIG:
      raise NotImplementedError('Unsupported platform: %s' % (key,))
    config = CHROME_CONFIG[key]
    gs_path = config['gs_path']
    app_name = config['app_name']
    chrome_archive_stem = config['chrome_archive_stem']
    chrome_driver_archive_stem = config['driver_archive_stem']

    chrome_zip = '%s.zip' % chrome_archive_stem
    chrome_driver_zip = '%s.zip' % chrome_driver_archive_stem
    api.gsutil.download(gs_bucket, '%s/%s' % (gs_path, version_file), chrome)
    version = api.file.read_text('read latest chrome version',
                                 chrome / version_file)
    chrome_output_path = download(api, gs_bucket,
                                  '%s/%s/%s' % (gs_path, version, chrome_zip),
                                  chrome)
    unzip_archive(api, chrome / chrome_zip, chrome_output_path)
    # TODO(cbruni): check if this is needed
    make_executable(api, chrome_output_path)
    chrome_app_path = chrome_output_path / chrome_archive_stem / app_name

    chromedriver_output_path = download(
        api, gs_bucket, '%s/%s/%s' % (gs_path, version, chrome_driver_zip),
        chrome)
    unzip_archive(api, chrome / chrome_driver_zip, chromedriver_output_path)
    chrome_driver_path = (
        chromedriver_output_path / chrome_driver_archive_stem / 'chromedriver')
    return chrome_app_path, chrome_driver_path


def download(api, gs_bucket, file_path, download_dir):
  api.gsutil.download(gs_bucket, file_path, download_dir)
  output_path = download_dir / 'zip'
  return output_path


def unzip_archive(api, archive_path, output):
  api.step('Extract archive', ['unzip', archive_path, '-d', output])


def make_executable(api, directory):
  api.step('Update permissions for %s' % directory,
           ['chmod', '-R', '+x', directory])
