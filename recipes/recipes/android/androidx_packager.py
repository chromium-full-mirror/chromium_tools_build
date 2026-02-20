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
    'recipe_engine/bcid_reporter',
    'recipe_engine/cipd',
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/led',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/step',
    'recipe_engine/time',
]

PROPERTIES = sdk_packager.InputProperties


def RollSubproject(api, subproject_name, subproject_path, roll_cmd):
  api.file.ensure_directory(f'Ensure {subproject_name} dir exists',
                            subproject_path)
  api.file.rmcontents(f'Cleanup old {subproject_name} contents',
                      subproject_path)

  if api.file.listdir('Ensure cleanup was successful', subproject_path):
    raise api.step.InfraFailure(
        f'Unable to delete {subproject_name}\'s old contents.')

  api.step('Run fetch_all script', roll_cmd)
  api.path.mock_add_paths(subproject_path / 'cipd.yaml')

  yaml_path = subproject_path / 'cipd.yaml'
  yaml_lines = api.file.read_text('Read cipd.yaml', yaml_path).split('\n')

  api.step.empty('Parse version from yaml')
  version = None
  for yaml_line in yaml_lines:
    tokens = yaml_line.split()
    if len(tokens) == 2 and tokens[0] == 'package:':
      package = tokens[1]
    if len(tokens) == 3 and yaml_line.startswith('# version: '):
      version = tokens[2]

  if version is None:
    cur_time = math.floor(api.time.time() / 60 / 60 / 24)
    version = 'cr-' + str(cur_time)
    api.step.empty('Version line not found, using current time')

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
    try:
      api.bcid_reporter.create_from_yaml(
          yaml_path,
          tags={
              'version': version,
              'details0': 'version-' + version
          },
          refs=['latest'])
    except api.step.StepFailure:
      # Do not fail the build due to cipd ACLs if we are on a shadow builder.
      if api.led.led_build:
        api.step.empty(
            'CIPD upload failed; ignoring because we are on shadow builder')
      else:
        raise


def RunSteps(api, properties):
  if not api.led.led_build:
    api.bcid_reporter.report_stage("start")

  api.gclient.set_config('chromium')
  api.gclient.apply_config('android')
  update_result = api.chromium_checkout.ensure_checkout()

  source_dir = update_result.source_root.path

  androidx_dir = source_dir / 'third_party/androidx'
  androidx_cipd_dir = androidx_dir / 'cipd'
  androidx_roll_cmd = [androidx_dir / 'fetch_all_androidx.py', '-v']
  with api.step.nest('Roll //third_party/androidx'):
    RollSubproject(api, 'androidx', androidx_cipd_dir, androidx_roll_cmd)

  autorolled_deps_dir = source_dir / 'third_party/android_deps/autorolled'
  autorolled_cipd_dir = autorolled_deps_dir / 'cipd'
  autorolled_roll_cmd = [autorolled_deps_dir / 'fetch_all_autorolled.py', '-v']
  with api.step.nest('Roll //third_party/android_deps/autorolled'):
    RollSubproject(api, 'autorolled', autorolled_cipd_dir, autorolled_roll_cmd)

  if not api.led.led_build:
    api.bcid_reporter.report_stage("upload-complete")


def GenTests(api):
  androidx_dir = api.path.checkout_dir.joinpath('third_party', 'androidx')
  androidx_sample_lib = androidx_dir.joinpath('libs', 'androidx_dino')
  androidx_step_prefix = 'Roll //third_party/androidx.'
  autorolled_step_prefix = 'Roll //third_party/android_deps/autorolled.'

  yield api.test(
      'basic',
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-androidx-packager'),
      api.path.exists(androidx_dir / 'fetch_all_androidx.py',
                      androidx_sample_lib / 'README.chromium'),
      api.override_step_data(
          f'{androidx_step_prefix}Read cipd.yaml',
          api.file.read_text('# version: cr-1\npackage: package1')),
      api.override_step_data(f'{androidx_step_prefix}cipd search package1 cr-1',
                             api.cipd.example_error('error')),
      api.post_process(post_process.MustRun,
                       f'{androidx_step_prefix}Run fetch_all script'),
      api.post_process(post_process.MustRun,
                       f'{androidx_step_prefix}register cipd.yaml'),
      api.override_step_data(
          f'{autorolled_step_prefix}Read cipd.yaml',
          api.file.read_text('# version: cr-1\npackage: package2')),
      api.override_step_data(
          f'{autorolled_step_prefix}cipd search package2 cr-1',
          api.cipd.example_error('error')),
      api.post_process(post_process.MustRun,
                       f'{autorolled_step_prefix}Run fetch_all script'),
      api.post_process(post_process.MustRun,
                       f'{autorolled_step_prefix}register cipd.yaml'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'directory-deletion-fails',
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-androidx-packager'),
      api.path.exists(androidx_dir / 'fetch_all_androidx.py',
                      androidx_sample_lib.joinpath('README.chromium')),
      api.override_step_data(
          f'{androidx_step_prefix}Ensure cleanup was successful',
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
      api.path.exists(androidx_dir / 'fetch_all_androidx.py',
                      androidx_sample_lib / 'README.chromium'),
      api.override_step_data(
          f'{androidx_step_prefix}Read cipd.yaml',
          api.file.read_text('# version: cr-1\npackage: package1')),
      api.override_step_data(f'{androidx_step_prefix}cipd search package1 cr-1',
                             api.cipd.example_search('package1', instances=1)),
      api.post_process(post_process.MustRun,
                       f'{androidx_step_prefix}Run fetch_all script'),
      api.post_process(post_process.DoesNotRun,
                       f'{androidx_step_prefix}register cipd.yaml'),
      api.override_step_data(
          f'{autorolled_step_prefix}Read cipd.yaml',
          api.file.read_text('# version: cr-1\npackage: package2')),
      api.override_step_data(
          f'{autorolled_step_prefix}cipd search package2 cr-1',
          api.cipd.example_search('package2', instances=1)),
      api.post_process(post_process.MustRun,
                       f'{autorolled_step_prefix}Run fetch_all script'),
      api.post_process(post_process.DoesNotRun,
                       f'{autorolled_step_prefix}register cipd.yaml'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'version_not_in_yaml',
      api.time.seed(314159),
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-androidx-packager'),
      api.path.exists(androidx_dir / 'fetch_all_androidx.py',
                      androidx_sample_lib / 'README.chromium'),
      api.override_step_data(f'{androidx_step_prefix}Read cipd.yaml',
                             api.file.read_text('package: package1')),
      api.override_step_data(f'{androidx_step_prefix}cipd search package1 cr-3',
                             api.cipd.example_error('error')),
      api.post_process(post_process.MustRun,
                       f'{androidx_step_prefix}Run fetch_all script'),
      api.post_process(post_process.MustRun,
                       f'{androidx_step_prefix}register cipd.yaml'),
      api.override_step_data(f'{autorolled_step_prefix}Read cipd.yaml',
                             api.file.read_text('package: package1')),
      api.override_step_data(
          f'{autorolled_step_prefix}cipd search package1 cr-3',
          api.cipd.example_error('error')),
      api.post_process(post_process.MustRun,
                       f'{autorolled_step_prefix}Run fetch_all script'),
      api.post_process(post_process.MustRun,
                       f'{autorolled_step_prefix}register cipd.yaml'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cipd_acl_fail',
      api.time.seed(314159),
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-androidx-packager'),
      api.path.exists(androidx_dir / 'fetch_all_androidx.py',
                      androidx_sample_lib / 'README.chromium'),
      api.override_step_data(
          f'{androidx_step_prefix}Read cipd.yaml',
          api.file.read_text('# version: cr-1\npackage: package1')),
      api.override_step_data(f'{androidx_step_prefix}cipd search package1 cr-1',
                             api.cipd.example_error('error')),
      api.post_process(post_process.MustRun,
                       f'{androidx_step_prefix}Run fetch_all script'),
      api.step_data(f'{androidx_step_prefix}register cipd.yaml', retcode=1),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'cipd_acl_no_fail_on_led',
      api.time.seed(314159),
      api.properties(**{
          '$recipe_engine/led': {
              'shadowed_bucket': 'try',
          },
      }),
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-androidx-packager'),
      api.path.exists(androidx_dir / 'fetch_all_androidx.py',
                      androidx_sample_lib / 'README.chromium'),
      api.override_step_data(
          f'{androidx_step_prefix}Read cipd.yaml',
          api.file.read_text('# version: cr-1\npackage: package1')),
      api.override_step_data(f'{androidx_step_prefix}cipd search package1 cr-1',
                             api.cipd.example_error('error')),
      api.post_process(post_process.MustRun,
                       f'{androidx_step_prefix}Run fetch_all script'),
      api.step_data(f'{androidx_step_prefix}register cipd.yaml', retcode=1),
      api.override_step_data(
          f'{autorolled_step_prefix}Read cipd.yaml',
          api.file.read_text('# version: cr-1\npackage: package2')),
      api.override_step_data(
          f'{autorolled_step_prefix}cipd search package2 cr-1',
          api.cipd.example_error('error')),
      api.step_data(f'{autorolled_step_prefix}register cipd.yaml', retcode=1),
      api.post_process(post_process.MustRun,
                       f'{autorolled_step_prefix}Run fetch_all script'),
      api.post_process(post_process.MustRun,
                       f'{autorolled_step_prefix}register cipd.yaml'),
      api.post_process(post_process.DropExpectation),
  )
