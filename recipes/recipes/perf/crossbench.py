# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
'''
Recipe for running Crossbench's End2End tests.
'''

import re
from urllib.parse import urlparse

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import code_coverage
from RECIPE_MODULES.depot_tools import bot_update, gclient, gsutil
from RECIPE_MODULES.infra import zip as zip_module
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cas,
  context,
  defer,
  file,
  path,
  platform,
  resultdb,
  step,
  url,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  cas: cas.API
  code_coverage: code_coverage.API
  context: context.API
  defer: defer.API
  file: file.API
  gclient: gclient.API
  gsutil: gsutil.API
  path: path.API
  platform: platform.API
  resultdb: resultdb.API
  step: step.API
  url: url.API
  zip: zip_module.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  context: context.TEST_API
  file: file.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  step: step.TEST_API
  url: url.TEST_API


CFT_LKGR_URL = 'https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json'
ALLOWED_CFT_BUCKET = 'chrome-for-testing-public'
ALLOWED_CFT_HOST = 'storage.googleapis.com'
MOCK_VER = '131.0.6778.204'
MOCK_URL = f'https://storage.googleapis.com/{ALLOWED_CFT_BUCKET}/{MOCK_VER}'
# The prefix is used in the tests to identify whether they are running in CQ.
_CAS_DIR_PREFIX = 'cq_archive_'
_COVERAGE_BUCKET = 'code-coverage-data'
_VERSION_RE = re.compile(r'^\d+(\.\d+)+$')


def _wrap_resultdb(api: DEPS, cmd, **kwargs):
  if api.resultdb.enabled:
    return api.resultdb.wrap(cmd, **kwargs)
  return cmd


def RunSteps(api: DEPS):
  api.gclient.set_config('crossbench')
  update_result = api.bot_update.ensure_checkout()
  api.gclient.runhooks()

  chrome_app_path, chrome_driver_path = download_chrome(api, 'Stable')

  cas_archive = api.path.mkdtemp(_CAS_DIR_PREFIX)
  lcov_file = cas_archive / 'lcov.info'
  with api.defer.context() as defer:
    try:
      # TODO(crbug.com/384926023): Unit tests are not ready to run on Windows
      # in CQ.
      if not api.platform.is_win:
        defer(
          api.step,
          'Run Unit Tests',
          _wrap_resultdb(
            api,
            [
              'vpython3',
              'crossbench/tests/crossbench/runner.py',
              f'--log-file={cas_archive}/pytest.tests.crossbench.out.txt',
              '--cov=crossbench',
              f'--cov-report=lcov:{lcov_file}',
            ],
          ),
        )

      e2e_cmd = [
        'vpython3',
        '-Xutf8',
        'crossbench/tests/end2end/runner.py',
        f'--test-browser-path={chrome_app_path}',
        f'--test-driver-path={chrome_driver_path}',
        f'--cas-archive={cas_archive}',
        f'--log-file={cas_archive}/pytest.tests.end2end.desktop.out.txt',
        '--ignore-tests=android',
      ]
      if not api.platform.is_win:
        e2e_cmd.extend(
          [
            '--cov=crossbench',
            '--cov-append',
            f'--cov-report=lcov:{lcov_file}',
          ]
        )
      defer(
        api.step,
        'Run End2End Tests',
        _wrap_resultdb(api, e2e_cmd),
      )
      defer(upload_coverage, api, update_result.source_root.path, cas_archive)
    finally:
      api.cas.archive('Copy End2End test logs to CAS', cas_archive, cas_archive)


def upload_coverage(api: DEPS, source_dir, cas_archive):
  if (
    not api.platform.is_linux or not api.buildbucket.build.input.gerrit_changes
  ):
    return
  with api.step.nest('Process coverage data'):
    api.step(
      'Sanitize lcov.info',
      ['sed', '-i', '/^DA:0,/d', cas_archive / 'lcov.info'],
    )
    diff_mapping_path = cas_archive / 'diff_mapping.json'
    api.file.write_json('Write diff mapping', diff_mapping_path, {})
    metadata_dir = cas_archive / 'coverage_metadata'
    api.file.ensure_directory('Ensure coverage metadata dir', metadata_dir)
    api.step(
      'Generate coverage metadata',
      [
        'vpython3',
        api.code_coverage.resource(
          'generate_coverage_metadata_for_javascript.py'
        ),
        '--src-path',
        source_dir,
        '--output-dir',
        metadata_dir,
        '--coverage-dir',
        cas_archive,
        '--diff-mapping-path',
        diff_mapping_path,
      ],
    )
    build = api.buildbucket.build
    change = build.input.gerrit_changes[0]
    builder = build.builder.builder
    gs_path = (
      f'presubmit/{change.host}/{change.change}/{change.patchset}/'
      f'{build.builder.bucket}/{builder}/{build.id}/metadata'
    )
    api.gsutil.upload(
      source=metadata_dir,
      bucket=_COVERAGE_BUCKET,
      dest=gs_path,
      args=['-r'],
      multithreaded=True,
      name='Upload coverage metadata',
      ok_ret='any',
    )
    result = api.step.active_result
    result.presentation.properties['coverage_gs_bucket'] = _COVERAGE_BUCKET
    result.presentation.properties['coverage_metadata_gs_paths'] = [gs_path]
    result.presentation.properties['mimic_builder_names'] = [builder]


def GenTests(api: TEST_DEPS):
  yield api.test('basic')
  yield api.test(
    'win-intel',
    api.platform('win', 64),
    api.platform.arch('intel'),
  )
  yield api.test(
    'linux-intel-64',
    api.buildbucket.try_build(
      project='crossbench', builder='e2e Linux x64 Try'
    ),
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
  yield api.test(
    'using-cache',
    api.platform('linux', 64),
    api.platform.arch('intel'),
    api.path.exists(
      api.path.cache_dir
      / f'builder/chrome/Stable/{MOCK_VER}/chrome/chrome-linux64/chrome',
    ),
  )
  yield api.test(
    'poc-poisoned-cft-manifest-untrusted-bucket',
    api.platform('linux', 64),
    api.platform.arch('intel'),
    api.url.json(
      'Get the latest Chrome Stable binary URL to download',
      {
        'channels': {
          'Stable': {
            'version': '131.0.6778.204',
            'downloads': {
              'chrome': [
                {
                  'platform': 'linux64',
                  'url': 'https://storage.googleapis.com/'
                  'attacker-controlled-public-bucket/'
                  '131.0.6778.204/linux64/chrome-linux64.zip',
                }
              ],
            },
          },
        },
      },
    ),
    api.expect_exception('ValueError'),
  )
  yield api.test(
    'poc-poisoned-cft-manifest-untrusted-host',
    api.platform('linux', 64),
    api.platform.arch('intel'),
    api.url.json(
      'Get the latest Chrome Stable binary URL to download',
      {
        'channels': {
          'Stable': {
            'version': '131.0.6778.204',
            'downloads': {
              'chrome': [
                {
                  'platform': 'linux64',
                  'url': 'https://evil.com/chrome-for-testing-public/'
                  '131.0.6778.204/linux64/chrome-linux64.zip',
                }
              ],
            },
          },
        },
      },
    ),
    api.expect_exception('ValueError'),
  )
  yield api.test(
    'poc-poisoned-cft-manifest-missing-platform',
    api.platform('linux', 64),
    api.platform.arch('intel'),
    api.url.json(
      'Get the latest Chrome Stable binary URL to download',
      {
        'channels': {
          'Stable': {
            'version': '131.0.6778.204',
            'downloads': {
              'chrome': [
                {
                  'platform': 'win64',
                  'url': 'https://storage.googleapis.com/'
                  'chrome-for-testing-public/'
                  '131.0.6778.204/win64/chrome-win64.zip',
                }
              ],
            },
          },
        },
      },
    ),
    api.expect_exception('ValueError'),
  )
  yield api.test(
    'poc-poisoned-cft-manifest-invalid-version',
    api.platform('linux', 64),
    api.platform.arch('intel'),
    api.url.json(
      'Get the latest Chrome Stable binary URL to download',
      {
        'channels': {
          'Stable': {
            'version': 'invalid_version',
            'downloads': {
              'chrome': [
                {
                  'platform': 'linux64',
                  'url': 'https://storage.googleapis.com/'
                  'chrome-for-testing-public/'
                  '131.0.6778.204/linux64/chrome-linux64.zip',
                }
              ],
            },
          },
        },
      },
    ),
    api.expect_exception('ValueError'),
  )
  yield api.test(
    'unit-tests-failure-defers',
    api.platform('linux', 64),
    api.platform.arch('intel'),
    api.step_data('Run Unit Tests', retcode=1),
    api.expect_status('FAILURE'),
  )


CHROME_CONFIG = {
  ('mac', 'arm', 64): {
    'cft_platform': 'mac-arm64',
    'chrome_archive_path': 'chrome-mac-arm64/Google Chrome for Testing.app',
    'driver_archive_path': 'chromedriver-mac-arm64/chromedriver',
  },
  ('mac', 'intel', 64): {
    'cft_platform': 'mac-x64',
    'chrome_archive_path': 'chrome-mac-x64/Google Chrome for Testing.app',
    'driver_archive_path': 'chromedriver-mac-x64/chromedriver',
  },
  ('linux', 'intel', 64): {
    'cft_platform': 'linux64',
    'chrome_archive_path': 'chrome-linux64/chrome',
    'driver_archive_path': 'chromedriver-linux64/chromedriver',
  },
  ('win', 'intel', 64): {
    'cft_platform': 'win64',
    'chrome_archive_path': 'chrome-win64/chrome.exe',
    'driver_archive_path': 'chromedriver-win64/chromedriver.exe',
  },
}


def _validate_cft_version(version):
  if not _VERSION_RE.match(version):
    raise ValueError(f'Invalid Chrome for Testing version: {version!r}')


def _validate_cft_url(url, expected_version, expected_platform, binary_name):
  parsed = urlparse(url)
  if parsed.scheme != 'https' or parsed.netloc != ALLOWED_CFT_HOST:
    raise ValueError(
      f'Untrusted CFT URL {url!r}: expected https://{ALLOWED_CFT_HOST}/...'
    )
  expected_zip = f'{binary_name}-{expected_platform}.zip'
  expected_path = f'/{ALLOWED_CFT_BUCKET}/{expected_version}/{expected_platform}/{expected_zip}'
  if parsed.path != expected_path:
    raise ValueError(
      f'Untrusted CFT URL path {parsed.path!r} in {url!r}: expected {expected_path!r}'
    )


def download_chrome(api: DEPS, channel):
  channel_info = api.url.get_json(
    CFT_LKGR_URL,
    step_name=f'Get the latest Chrome {channel} binary URL to download',
    default_test_data={
      'channels': {
        'Stable': {
          'version': MOCK_VER,
          'downloads': {
            'chrome': [
              {
                'platform': 'linux64',
                'url': f'{MOCK_URL}/linux64/chrome-linux64.zip',
              },
              {
                'platform': 'mac-arm64',
                'url': f'{MOCK_URL}/mac-arm64/chrome-mac-arm64.zip',
              },
              {
                'platform': 'mac-x64',
                'url': f'{MOCK_URL}/mac-x64/chrome-mac-x64.zip',
              },
              {
                'platform': 'win64',
                'url': f'{MOCK_URL}/win64/chrome-win64.zip',
              },
            ]
          },
        }
      }
    },
  ).output['channels'][channel]
  version = channel_info['version']
  _validate_cft_version(version)
  chrome_dir = api.path.cache_dir.joinpath(
    'builder', 'chrome', channel, version
  )
  api.file.ensure_directory('Init cache if not exists', chrome_dir)
  with api.context(cwd=chrome_dir):
    chrome_output_path = chrome_dir / 'chrome'
    driver_output_path = chrome_dir / 'chromedriver'
    config_key = (api.platform.name, api.platform.arch, api.platform.bits)
    if config_key not in CHROME_CONFIG:
      raise NotImplementedError('Unsupported platform: %s' % (config_key,))
    config = CHROME_CONFIG[config_key]
    chrome_app_path = chrome_output_path / config['chrome_archive_path']
    chrome_driver_path = driver_output_path / config['driver_archive_path']
    if api.path.exists(chrome_app_path):
      return chrome_app_path, chrome_driver_path
    downloads = channel_info['downloads']['chrome']
    with api.step.nest(f'Download Chrome {channel} {version}'):
      cft_platform = config['cft_platform']
      urls = [x['url'] for x in downloads if x['platform'] == cft_platform]
      if not urls:
        raise ValueError(
          f'No CFT download URL found for platform {cft_platform!r}'
        )
      url = urls[0]
      _validate_cft_url(url, version, cft_platform, 'chrome')

      # Downloading and unzipping the latest Chrome binary
      chrome_zip = f'chrome-{cft_platform}.zip'
      api.gsutil.download_url(url, chrome_dir)
      unzip_archive(api, chrome_dir / chrome_zip, chrome_output_path)

      # Downloading and unzipping the latest ChromeDriver
      chromedriver_zip = f'chromedriver-{cft_platform}.zip'
      chromedriver_url = url.replace(chrome_zip, chromedriver_zip)
      _validate_cft_url(chromedriver_url, version, cft_platform, 'chromedriver')
      api.gsutil.download_url(chromedriver_url, chrome_dir)
      unzip_archive(api, chrome_dir / chromedriver_zip, driver_output_path)
      return chrome_app_path, chrome_driver_path


def unzip_archive(api: DEPS, archive_path, output):
  api.zip.unzip('Extract archive', archive_path, output, quiet=True)
