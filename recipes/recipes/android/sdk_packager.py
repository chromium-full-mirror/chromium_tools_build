# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Packages Android SDK packages as CIPD packages."""

import textwrap

from recipe_engine import post_process
from PB.recipe_engine import result as result_pb
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipes.build.android import sdk_packager

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/gclient',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]


PROPERTIES = sdk_packager.InputProperties


def RunSteps(api, properties):
  api.gclient.set_config('chromium')
  api.gclient.apply_config('android')
  api.chromium_checkout.ensure_checkout()

  sdk_manager = api.path['checkout'].join('third_party', 'android_sdk',
                                          'public', 'cmdline-tools', 'latest',
                                          'bin', 'sdkmanager')
  jdk_path = api.path['checkout'].join('third_party', 'jdk', 'current')

  if not api.path.exists(sdk_manager):
    summary_markdown = (
        'Unable to find sdkmanager at path `%s`' % str(sdk_manager))
    return result_pb.RawResult(
        status=common_pb.INFRA_FAILURE,
        summary_markdown=summary_markdown)

  with api.step.nest('package versions'):
    list_cmd = [
        sdk_manager,
        '--list',
        '--verbose',
    ]
    # The sdkmanager script requires a JDK newer than 1.8, and on the bot the
    # default JDK is 1.8, so we use the one bundled in Chromium (1.17).
    with api.context(env={'JAVA_HOME': str(jdk_path)}):
      list_output = api.step(
          'list', list_cmd, stdout=api.raw_io.output_text()).stdout

    parse_result = api.step('parse', [
        'python3',
        api.resource('parse_sdkmanager_list.py'),
        '--raw-input',
        api.raw_io.input_text(list_output),
        '--json-output',
        api.json.output(),
    ])
    if not parse_result.json.output:
      return result_pb.RawResult(
          status=common_pb.INFRA_FAILURE,
          summary_markdown='Unable to parse sdkmanager output.')
    packages_by_name = {
        p['name']: p
        for p in parse_result.json.output.get('available', [])
    }

  for package in properties.packages:
    cipd_yaml = api.path['checkout'].join(package.cipd_yaml)
    if not api.path.exists(cipd_yaml):
      summary_markdown = (
          'Unable to find yaml file for %s at path `%s`' % (
              package.sdk_package_name,
              cipd_yaml))
      return result_pb.RawResult(
          status=common_pb.INFRA_FAILURE,
          summary_markdown=summary_markdown)

    with api.step.nest(package.sdk_package_name):
      install_cmd = [
          sdk_manager,
          '--install',
          package.sdk_package_name,
      ]
      with api.context(env={'JAVA_HOME': str(jdk_path)}):
        api.step(
            'install',
            install_cmd,
            # Accept the license agreement, if necessary.
            stdin=api.raw_io.input_text('y'))
      tags = {}
      package_version = (
          packages_by_name.get(package.sdk_package_name, {}).get('version'))
      if package_version:
        tags['version'] = package_version
      api.cipd.create_from_yaml(cipd_yaml, tags=tags, refs=['latest'])


def GenTests(api):
  emulator_package_properties = (
      api.properties(
          packages=[
              {
                  'sdk_package_name': 'emulator',
                  'cipd_yaml': 'third_party/android_sdk/public/emulator.yaml',
              }
          ])
  )

  def package_version_steps():
    return (api.override_step_data(
        'package versions.list',
        stdout=api.raw_io.output_text(
            textwrap.dedent('''\
                Available Packages:
                -------------------
                emulator
                    Description: Android Emulator
                    Version:     29.0.11
                '''))) + api.override_step_data(
                'package versions.parse',
                api.json.output({
                    'available': [{
                        'name': 'emulator',
                        'description': 'Android Emulator',
                        'version': '29.0.11',
                        'installed location': None,
                    },],
                    'installed': [],
                })))

  yield api.test(
      'basic',
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-sdk-packager'),
      emulator_package_properties,
      api.path.exists(
          api.path['checkout'].join('third_party', 'android_sdk', 'public',
                                    'cmdline-tools', 'latest', 'bin',
                                    'sdkmanager'),
          api.path['checkout'].join('third_party', 'android_sdk', 'public',
                                    'emulator.yaml')),
      package_version_steps(),
      api.post_process(post_process.MustRun, 'emulator.install'),
      api.post_process(post_process.MustRun, 'emulator.create emulator.yaml'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no-sdkmanager',
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-sdk-packager'),
      emulator_package_properties,
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'unparseable-list-output',
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-sdk-packager'),
      emulator_package_properties,
      api.path.exists(api.path['checkout'].join('third_party', 'android_sdk',
                                                'public', 'cmdline-tools',
                                                'latest', 'bin', 'sdkmanager')),
      api.override_step_data(
          'package versions.list',
          stdout=api.raw_io.output_text(
              textwrap.dedent('''\
              [UNPARSEABLE]
              '''))),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.ResultReason,
                       'Unable to parse sdkmanager output.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no-cipd-yaml',
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-sdk-packager'),
      emulator_package_properties,
      api.path.exists(api.path['checkout'].join('third_party', 'android_sdk',
                                                'public', 'cmdline-tools',
                                                'latest', 'bin', 'sdkmanager')),
      package_version_steps(),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.ResultReasonRE, 'Unable to find yaml file'),
      api.post_process(post_process.DropExpectation),
  )
