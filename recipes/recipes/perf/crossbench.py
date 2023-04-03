# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Recipe for running Crossbench's End2End tests.
"""

DEPS = [
    'depot_tools/bot_update',
    'depot_tools/gclient',
    'depot_tools/gsutil',
    'recipe_engine/file',
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


def download_chrome(api):
  """
  Download the latest chrome binaries
  """
  with api.step.nest('Download Chrome Binaries'):
    chrome = api.path.mkdtemp(prefix='chrome')
    gs_bucket = 'chromium-browser-snapshots'
    gs_path = 'Mac'
    version_file = 'LAST_CHANGE'
    chrome_zip = 'chrome-mac.zip'
    chrome_driver_zip = 'chromedriver_mac64.zip'
    api.gsutil.download(gs_bucket, '%s/%s' % (gs_path, version_file), chrome)
    version = api.file.read_text('read latest chrome version',
                                 chrome / version_file)
    chrome_output_path = download(api, gs_bucket,
                                  '%s/%s/%s' % (gs_path, version, chrome_zip),
                                  chrome)
    unzip_archive(api, chrome / chrome_zip, chrome_output_path)
    update_permissions(api, chrome_output_path)
    chrome_app_path = chrome_output_path / 'chrome-mac' / 'Chromium.app'

    chromedriver_output_path = download(
        api, gs_bucket, '%s/%s/%s' % (gs_path, version, chrome_driver_zip),
        chrome)
    unzip_archive(api, chrome / chrome_driver_zip, chromedriver_output_path)
    chrome_driver_path = (
        chromedriver_output_path / 'chromedriver_mac64' / 'chromedriver')
    return chrome_app_path, chrome_driver_path


def download(api, gs_bucket, file_path, download_dir):
  api.gsutil.download(gs_bucket, file_path, download_dir)
  output_path = download_dir / 'zip'
  return output_path


def unzip_archive(api, archive_path, output):
  api.step('Extract archive', ['unzip', archive_path, '-d', output])


def update_permissions(api, directory):
  api.step('Update permissions for %s' % directory,
           ['chmod', '-R', '+x', directory])
