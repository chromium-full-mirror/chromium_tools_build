# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""This script stores constants accessed from the recipe API."""

DEFAULT_BUCKET_NAME = 'code-coverage-data'

DEFAULT_FUZZ_SRC_BUCKET_NAME = 'locally-generated-coverage-reports'
DEFAULT_FUZZ_SRC_PROFDATA_FILE_NAME = 'latest_profdata.json'
DEFAULT_FUZZ_SRC_BUILD_ARCHIVE_NAME = 'latest_build_archive.zip'
DEFAULT_FUZZ_SRC_LLVM_COV_NAME = 'llvm-cov'
DEFAULT_FUZZ_SRC_SRC_ARCHIVE_NAME = 'latest_src_archive.zip'

# GCS bucket corresponding to go/kalypsi. Uploading data to this bucket
# enables showing coverage metrics in code search.
ZOSS_BUCKET_NAME = "ng3-metrics"

# Controls metadata fields for the data to be exported to zoss
# Each element represents different codesearch instance where
# we want code coverage data to be available.
#
# TODO(crbug/1466708): At present there are no coverage builders which generate
# coverage for "chrome-internal". All builders generate coverage on "chromium"
# i.e. publicly visible code. Therefore we export the chromium coverage
# data itself to chrome-internal.
# At some point, when we have chrome-internal coverage builders, their data
# should flow to chrome-internal repo.
COVERAGE_REPOS = [{
    'host': 'chrome-internal',
    'project': 'codesearch/chrome/src',
}, {
    'host': 'chromium',
    'project': 'codesearch/chromium/src',
}]

# Name of the file to store the directory metadata.
DIR_METADATA_FILE_NAME = 'dir_metadata.json'

# Name of the file to store the line number mapping from bot to Gerrit.
BOT_TO_GERRIT_LINE_NUM_MAPPING_FILE_NAME = (
    'bot_to_gerrit_line_num_mapping.json')

# Names of the files to store reused Quick Run coverage data as
QUICK_RUN_UNIT_PROFDATA = 'quick_run_merged_unittest.profdata'
QUICK_RUN_OVERALL_PROFDATA = 'quick_run_merged.profdata'

# Valid extensions of source files that supported per coverage tool.
TOOLS_TO_EXTENSIONS_MAP = {
    'clang': [
        '.mm', '.S', '.c', '.hh', '.cxx', '.hpp', '.cc', '.cpp', '.ipp', '.h',
        '.m', '.hxx'
    ],
    'jacoco': ['.java'],
    'v8': ['.js', '.ts']
}

# Regex to identify files to be excluded from coverage
# It includes all those files
# - with 'tests/' or 'testing/' in their path
# - with filename ending with 'Test', 'Tests', 'test' or 'tests'
EXCLUDED_FILE_REGEX = r'(^|.+\/)test(s|ing)?\/.+|.+(T|t)ests?\..*'

# Third party code is excluded by default.
# List of paths which are not to be excluded.
INCLUDED_THIRD_PARTY_SUBDIRS = [
    'third_party/angle',
    'third_party/blink',
    'third_party/dawn',
    'third_party/liburlpattern',
    'third_party/pdfium',
    'third_party/swiftshader',
    'third_party/wpt_tools',
    'third_party/zlib',
]

# Only generate coverage data for CLs in these gerrit projects.
# This is a list of (host, project) pairs
SUPPORTED_PATCH_PROJECTS = [('chromium-review.googlesource.com',
                             'chromium/src'),
                            ('webrtc-review.googlesource.com', 'src')]

# A list of test types supported by code coverage api. The test types are
# defined as str literals and used across multiple places.
SUPPORTED_TEST_TYPES = ['overall', 'unit']

# A mapping of platform to test target names regex. This is used to filter
# the test targets to be run for a certain test type. See SUPPORTED_TEST_TYPES
# above.

# Keys in the dict are platforms that support multiple test types. If a platform
# isn't in the keys, the default test type will be "overall" and the test
# pattern will be '.+' i.e. all tests would run. All unit regex's must match
# QUICK_RUN_UNIT_PROFDATA to allow merging of previous Quick Runs with their
# inverted run
PLATFORM_TO_TARGET_NAME_PATTERN_MAP = {
    'ios': {
        'unit': '(boringssl_crypto_|boringssl_ssl_'
                '|.+_unit)tests',
        'overall': '.+'
    },
    'linux': {
        'unit': '(absl_hardening|blink_python|boringssl_crypto|boringssl_ssl'
                '|content_shell_crash|crashpad|cronet|ipc|metrics_python'
                '|vr_pixel|.*unit).*test.*',
        'overall': '.+'
    },
    'mac': {
        'unit': '(absl_hardening|boringssl_crypto|boringssl_ssl|crashpad'
                '|cronet|ipc|.*unit).*test.*',
        'overall': '.+'
    },
    'win': {
        'unit': '(absl_hardening|boringssl_crypto|boringssl_ssl|crashpad'
                '|cronet|ipc|vr_pixel|.*unit).*test.*',
        'overall': '.+'
    },
    'chromeos': {
        'unit': '(crashpad|ipc|.*unit).*test.*',
        'overall': '.+'
    },
    'android': {
        'unit': '(.*unit).*test.*',
        'overall': '.+'
    }
}
PLATFORM_TO_TARGET_NAME_PATTERN_MAP[
    'linux64'] = PLATFORM_TO_TARGET_NAME_PATTERN_MAP['linux']

BINARY_RELATIVE_PATHS_JSON_FILE_NAME = 'binary_relative_paths_for_clang_code_coverage.json'
