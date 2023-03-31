# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Packages Androidx libraries as CIPD packages."""

from recipe_engine import post_process
from PB.recipe_engine import result as result_pb
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipes.build.android import sdk_packager

import math

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/time',
]

PROPERTIES = sdk_packager.InputProperties


def RunSteps(api, properties):
  api.gclient.set_config('chromium')
  api.gclient.apply_config('android')
  api.chromium_checkout.ensure_checkout()

  androidx_dir = api.path['checkout'].join('third_party', 'androidx')
  androidx_libs_dir = androidx_dir.join('libs')

  api.file.ensure_directory('ensure libs dir exists', androidx_libs_dir)
  api.file.rmcontents('delete libs dir contents', androidx_libs_dir)

  if api.file.listdir('check libs empty', androidx_libs_dir):
    return result_pb.RawResult(
        status=common_pb.INFRA_FAILURE,
        summary_markdown='Unable to delete androidx libs directory.')

  fetch_all_cmd = androidx_dir.join('fetch_all_androidx.py')
  api.step('fetch_all', [fetch_all_cmd, '-v'])
  api.path.mock_add_paths(androidx_dir.join('cipd.yaml'))

  yaml_path = androidx_dir.join('cipd.yaml')
  yaml_lines = api.file.read_text('read cipd.yaml', yaml_path).split('\n')

  api.step('extract version', None)
  version_num = math.floor(api.time.time() / 60 / 60 / 24)
  version = 'cr-' + str(version_num)
  for yaml_line in yaml_lines:
    tokens = yaml_line.split()
    if len(tokens) == 2 and tokens[0] == 'package:':
      package = tokens[1]
    if len(tokens) == 3 and yaml_line.startswith('# version: cr-'):
      version = tokens[2]

  cipd_search_name = 'cipd search %s %s' % (package, version)
  cipd_search_cmd = [
      'cipd',
      'search',
      package,
      '-tag',
      'version:' + version,
      '-json-output',
      api.json.output()
  ]
  cipd_search_results = api.step(
      cipd_search_name, cipd_search_cmd, ok_ret='any').json.output['result']
  if not cipd_search_results or not 'instance_id' in cipd_search_results[0]:
    api.cipd.create_from_yaml(
        yaml_path,
        tags={
            'version': version,
            'details0': 'version-' + version
        },
        refs=['latest'])


def GenTests(api):
  androidx_dir = api.path['checkout'].join('third_party', 'androidx')
  androidx_sample_lib = androidx_dir.join('libs', 'androidx_dino')

  yield api.test(
      'basic',
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-androidx-packager'),
      api.path.exists(
          androidx_dir.join('fetch_all_androidx.py'),
          androidx_sample_lib.join('README.chromium')),
      api.override_step_data(
          'read cipd.yaml',
          api.file.read_text('# version: cr-1\npackage: package1')),
      api.override_step_data('cipd search package1 cr-1',
                             api.cipd.example_error('error')),
      api.post_process(post_process.MustRun, 'fetch_all'),
      api.post_process(post_process.MustRun, 'create cipd.yaml'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'directory-deletion-fails',
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-androidx-packager'),
      api.path.exists(
          androidx_dir.join('fetch_all_androidx.py'),
          androidx_sample_lib.join('README.chromium')),
      api.override_step_data('check libs empty',
                             api.file.listdir(['androidx_dino/cipd.yaml'])),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'version_already_uploaded',
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-androidx-packager'),
      api.path.exists(
          androidx_dir.join('fetch_all_androidx.py'),
          androidx_sample_lib.join('README.chromium')),
      api.override_step_data(
          'read cipd.yaml',
          api.file.read_text('# version: cr-1\npackage: package1')),
      api.override_step_data('cipd search package1 cr-1',
                             api.cipd.example_search('package1', instances=1)),
      api.post_process(post_process.MustRun, 'fetch_all'),
      api.post_process(post_process.DoesNotRun, 'create cipd.yaml'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'version_not_in_yaml',
      api.time.seed(314159),
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-androidx-packager'),
      api.path.exists(
          androidx_dir.join('fetch_all_androidx.py'),
          androidx_sample_lib.join('README.chromium')),
      api.override_step_data('read cipd.yaml',
                             api.file.read_text('package: package1')),
      api.override_step_data('cipd search package1 cr-3',
                             api.cipd.example_error('error')),
      api.post_process(post_process.MustRun, 'fetch_all'),
      api.post_process(post_process.MustRun, 'create cipd.yaml'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )
