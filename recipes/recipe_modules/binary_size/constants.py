# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations


DEFAULT_ANALYZE_TARGETS = [
  '//chrome/android:trichrome_32_minimal_apks',
  '//tools/binary_size:binary_size_trybot_py',
]
DEFAULT_COMPILE_TARGETS = [
  'trichrome_32_minimal_apks',
  'trichrome_library_64_apk',
]

# Path is relative to Chromium output directory.
DEFAULT_SIZE_CONFIG_JSON = 'config/Trichrome_size_config.json'

EXPECTATIONS_STEP_NAME = 'Checking for expectation failures'

PATCH_FIXED_BUILD_STEP_NAME = (
  'Not measuring binary size because build is broken without patch.'
)
RESULTS_GS_BUCKET = 'chromium-binary-size-trybot-results'
ARCHIVED_URL_FMT = 'https://storage.googleapis.com/{bucket}/{dest}'

RESULT_JSON_STEP_NAME = 'Read diff results'
RESULTS_STEP_NAME = 'Trybot Results'
PLUGIN_OUTPUT_PROPERTY_NAME = 'binary_size_plugin'

COMMIT_POSITION_FOOTER_KEY = 'Cr-Commit-Position'
ANDROID_BINARY_SIZE_FOOTER_KEY = 'Binary-Size'
FUCHSIA_BINARY_SIZE_FOOTER_KEY = 'Fuchsia-Binary-Size'
SKIP_EXPECTATIONS_FOOTER_KEY = 'Skip-Expectations'

FAILED_CHECK_MESSAGE = 'Failed Checks. See Failing steps for details'

TEST_TIME = 1454371200
TEST_BUILDER = 'android_binary_size'
TEST_BUILDNUMBER = 200
TEST_TIME_FMT = '2016/02/02'
TEST_RECENT_UPLOAD_CP = 862979
TEST_PATCH_PARENT_CP = 862960
TEST_RESULT_JSON = {
  'status_code': 0,
  'summary': '\n!summary!',
  'archive_filenames': ['result.ndjson', 'result.txt'],
  'links': [
    {
      'name': 'Resource Sizes Diff (high-level metrics)',
      'lines': ['!resource_sizes!'],
      'log_name': 'resource_sizes_log',
    },
    {
      'name': 'SuperSize Text Diff',
      'lines': ['!supersize text with \u0394!'],
    },
    {
      'name': 'Dex Method Diff',
      'lines': ['!dex_methods!'],
      'log_name': 'dex_methods_log',
    },
    {
      'name': 'Supersize HTML Diff',
      'url': 'https://foo.com/{{result.ndjson}}',
    },
  ],
  'gerrit_plugin_details': {
    'listings': [
      {
        'name': 'Normalised APK size',
        'delta': '500 bytes',
        'allowed': True,
        'log_name': 'resource_sizes_log',
      },
    ],
    'extras': [
      {
        'text': 'Supersize HTML Diff',
        'url': 'https://foo.com/{{result.ndjson}}',
      },
      {
        'text': 'SuperSize Text Diff',
        'url': '{{result.txt}}',
      },
    ],
  },
}

# Failure codes for Fuchsia binary size
FUCHSIA_SIZE_FAILURE = 1
FUCHSIA_ROLLER_WARNING = 2
FUCHSIA_AUTHOR_FLOW_MILESTONE = 107
