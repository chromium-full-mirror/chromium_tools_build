# Copyright 2019 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Packages Android SDK packages as CIPD packages."""

from collections import defaultdict
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
    'recipe_engine/file',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
]


PROPERTIES = sdk_packager.InputProperties

# The sdk root we use to install the packages
SDK_ROOT = ('third_party', 'android_sdk', 'public')


def RunSteps(api, properties):
  api.gclient.set_config('chromium')
  api.gclient.apply_config('android')
  update_result = api.chromium_checkout.ensure_checkout()
  source_dir = update_result.source_root.path

  sdk_root = source_dir.joinpath(*SDK_ROOT)
  cmdline_tools = sdk_root.joinpath('cmdline-tools', 'latest')
  android_cli = cmdline_tools.joinpath('bin', 'android')
  if not api.path.exists(android_cli):
    summary_markdown = ('Unable to find android_cli at path `%s`' %
                        str(android_cli))
    return result_pb.RawResult(
        status=common_pb.INFRA_FAILURE,
        summary_markdown=summary_markdown)
  # Copy cmdline-tools to a temp place to avoid it being uninstalled.
  temp_dir = api.path.mkdtemp('tmp')
  temp_cmdline_tools = temp_dir.joinpath('cmdline-tools')
  api.file.copytree('copy cmdline-tools', cmdline_tools, temp_cmdline_tools)

  # Remove all existing sdk packages to avoid errors during migration from
  # sdkmanager to android cli. See https://crbug.com/550431997#comment3
  with api.step.nest('remove existing packages'):
    children = api.file.listdir('list existing packages', sdk_root)
    for child in children:
      api.file.rmtree(f'remove {child}', child)

  # Use dict to keep the insertion order.
  package_dict = defaultdict(list)
  for package in properties.packages:
    key_tuple = (sdk_packager.SdkChannel.Name(package.sdk_channel),
                 sdk_packager.TargetOs.Name(package.target_os),
                 sdk_packager.TargetArch.Name(package.target_arch))
    package_dict[key_tuple].append(package)

  for key_tuple, packages in package_dict.items():
    channel, target_os, target_arch = key_tuple
    step_name = 'Process %s channel for %s %s' % (channel, target_os,
                                                  target_arch)
    with api.step.nest(step_name) as p_step:
      p_step.logs['packages'] = '%r' % packages
      result = _process_packages(api, temp_cmdline_tools, source_dir, packages,
                                 channel, target_os, target_arch)
      if result:
        return result


def _process_packages(api, cmdline_tools_dir, source_dir, packages, channel,
                      target_os, target_arch):
  """Process packages from a given list.

  Including use sdkmanager to fetch the package versions, and install packages
  with given channel and target_os, and upload to CIPD with related tags.

  Args:
    api - The recipe API object.
    cmdline_tools_dir - The directory of cmdline-tools. Should be a different
      copy than the chromium src, otherwise it may delete itself during update.
    source_dir - The directory of chromium src checkout. We use this to locate
      the jdk path, and cipd yaml file path.
    packages - A list of package names to install.
    channel - The channel of the packages, i.e. STABLE, BETA, DEV, CANARY.
    target_os - The target os for the packages, i.e. linux, mac, windows.
    target_arch - The target arch for the packages, i.e. x86_64, arm64.
  """
  # Setup the extra environment variables for sdkmanager
  env = {
      # Use the JDK from chromium repo to avoid out-of-date JDK on bot.
      'JAVA_HOME': str(source_dir.joinpath('third_party', 'jdk', 'current')),
  }
  android_cli = cmdline_tools_dir.joinpath('bin', 'android')
  sdk_root = source_dir.joinpath(*SDK_ROOT)
  sdk_cmd = [android_cli, f'--sdk={sdk_root}', 'sdk']

  packages_by_name = {}
  with api.step.nest('package versions'):
    list_cmd = ['list', '--all']
    if channel == 'BETA':
      list_cmd.append('--beta')
    elif channel == 'CANARY':
      list_cmd.append('--canary')
    with api.context(env=env):
      list_output = api.step(
          'list', sdk_cmd + list_cmd, stdout=api.raw_io.output_text()).stdout

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
          summary_markdown='Unable to parse android cli output.')
    for p in parse_result.json.output.get('available', []):
      packages_by_name[p['name']] = p

  for package in packages:
    cipd_yaml = source_dir.joinpath(package.cipd_yaml)
    if not api.path.exists(cipd_yaml):
      summary_markdown = (
          'Unable to find yaml file for %s at path `%s`' % (
              package.sdk_package_name,
              cipd_yaml))
      return result_pb.RawResult(
          status=common_pb.INFRA_FAILURE,
          summary_markdown=summary_markdown)

    with api.step.nest(package.sdk_package_name):
      # Uninstall first to remove potential installation from previous attempt.
      uninstall_cmd = [
          'remove',
          package.sdk_package_name,
      ]
      with api.context(env=env):
        api.step('cleanup', sdk_cmd + uninstall_cmd)

      install_cmd = ['install']
      if channel == 'BETA':
        install_cmd.append('--beta')
      elif channel == 'CANARY':
        install_cmd.append('--canary')

      install_cmd.append(f'--platform={target_os}_{target_arch}')
      install_cmd.append(package.sdk_package_name)

      with api.context(env=env):
        api.step(
            'install',
            sdk_cmd + install_cmd,
            # Accept the license agreement, if necessary.
            stdin=api.raw_io.input_text('y'))
      tags = {
          'channel': channel,
          'target_os': target_os,
          'target_arch': target_arch,
      }
      package_version = (
          packages_by_name.get(package.sdk_package_name, {}).get('version'))
      refs = ['latest']
      if package_version:
        tags['version'] = package_version
        # Also publish a per-version ref so ${platform}/${os} DEPS pins resolve
        # to a single instance (a version tag can match multiple instances).
        refs.append('version_%s' % package_version)
      api.cipd.create_from_yaml(cipd_yaml, tags=tags, refs=refs)


def GenTests(api):
  emulator_package_properties = api.properties(packages=[
      {
          'sdk_package_name': 'emulator',
          'cipd_yaml': 'third_party/android_sdk/public/emulator.yaml',
      },
      {
          'sdk_package_name': 'emulator',
          'cipd_yaml': 'third_party/android_sdk/public/emulator.yaml',
          'sdk_channel': 'BETA',
      },
      {
          'sdk_package_name': 'emulator',
          'cipd_yaml': 'third_party/android_sdk/public/mac/emulator.yaml',
          'sdk_channel': 'CANARY',
          'target_os': 'mac',
          'target_arch': 'arm64',
      },
  ])

  def package_version_stable_steps():
    data = api.override_step_data(
        'Process STABLE channel for linux x86_64.package versions.list',
        stdout=api.raw_io.output_text(
            textwrap.dedent('''\
                Available Packages:
                -------------------
                emulator
                    Description: Android Emulator
                    Version:     29.0.11
                ''')))
    data += api.override_step_data(
        'Process STABLE channel for linux x86_64.package versions.parse',
        api.json.output({
            'available': [{
                'name': 'emulator',
                'description': 'Android Emulator',
                'version': '29.0.11',
                'installed location': None,
            },],
            'installed': [],
        }))
    return data

  def package_version_beta_steps():
    data = api.override_step_data(
        'Process BETA channel for linux x86_64.package versions.list',
        stdout=api.raw_io.output_text(
            textwrap.dedent('''\
                Available Packages:
                -------------------
                emulator
                    Description: Android Emulator
                    Version:     31.0.15
                ''')))
    data += api.override_step_data(
        'Process BETA channel for linux x86_64.package versions.parse',
        api.json.output({
            'available': [{
                'name': 'emulator',
                'description': 'Android Emulator',
                'version': '31.0.15',
                'installed location': None,
            },],
            'installed': [],
        }))
    return data

  def package_version_canary_steps():
    data = api.override_step_data(
        'Process CANARY channel for mac arm64.package versions.list',
        stdout=api.raw_io.output_text(
            textwrap.dedent('''\
                Available Packages:
                -------------------
                emulator
                    Description: Android Emulator
                    Version:     32.0.15
                ''')))
    data += api.override_step_data(
        'Process CANARY channel for mac arm64.package versions.parse',
        api.json.output({
            'available': [{
                'name': 'emulator',
                'description': 'Android Emulator',
                'version': '32.0.15',
                'installed location': None,
            },],
            'installed': [],
        }))
    return data

  yield api.test(
      'basic',
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-sdk-packager'),
      api.step_data(
          'remove existing packages.list existing packages',
          api.file.listdir([
              '[CACHE]/builder/src/third_party/android_sdk/public/platforms'
          ])),
      api.post_process(post_process.MustRun, 'copy cmdline-tools'),
      emulator_package_properties,
      api.path.exists(
          api.path.checkout_dir.joinpath('third_party', 'android_sdk', 'public',
                                         'cmdline-tools', 'latest', 'bin',
                                         'android'),
          api.path.checkout_dir.joinpath('third_party', 'android_sdk', 'public',
                                         'emulator.yaml'),
          api.path.checkout_dir.joinpath('third_party', 'android_sdk', 'public',
                                         'mac', 'emulator.yaml')),
      package_version_stable_steps(),
      api.post_process(
          post_process.MustRun,
          'Process STABLE channel for linux x86_64.emulator.cleanup'),
      api.post_process(
          post_process.MustRun,
          'Process STABLE channel for linux x86_64.emulator.install'),
      api.post_process(
          post_process.MustRun,
          'Process STABLE channel for linux x86_64.emulator.create emulator.yaml'
      ),
      api.post_process(
          post_process.StepCommandContains,
          'Process STABLE channel for linux x86_64.emulator.create emulator.yaml',
          ['-ref', 'version_29.0.11']),
      package_version_beta_steps(),
      api.post_process(
          post_process.MustRun,
          'Process BETA channel for linux x86_64.emulator.cleanup'),
      api.post_process(
          post_process.MustRun,
          'Process BETA channel for linux x86_64.emulator.install'),
      api.post_process(
          post_process.MustRun,
          'Process BETA channel for linux x86_64.emulator.create emulator.yaml'
      ),
      package_version_canary_steps(),
      api.post_process(post_process.MustRun,
                       'Process CANARY channel for mac arm64.emulator.cleanup'),
      api.post_process(post_process.MustRun,
                       'Process CANARY channel for mac arm64.emulator.install'),
      api.post_process(
          post_process.MustRun,
          'Process CANARY channel for mac arm64.emulator.create emulator.yaml'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no-android-cli',
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
      api.path.exists(
          api.path.checkout_dir.joinpath('third_party', 'android_sdk', 'public',
                                         'cmdline-tools', 'latest', 'bin',
                                         'android')),
      api.override_step_data(
          'Process STABLE channel for linux x86_64.package versions.list',
          stdout=api.raw_io.output_text(
              textwrap.dedent('''\
              [UNPARSEABLE]
              '''))),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.SummaryMarkdown,
                       'Unable to parse android cli output.'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no-cipd-yaml',
      api.buildbucket.ci_build(
          project='chromium',
          git_repo='https://chromium.googlesource.com/chromium/src',
          builder='android-sdk-packager'),
      emulator_package_properties,
      api.path.exists(
          api.path.checkout_dir.joinpath('third_party', 'android_sdk', 'public',
                                         'cmdline-tools', 'latest', 'bin',
                                         'android')),
      package_version_stable_steps(),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.SummaryMarkdownRE,
                       'Unable to find yaml file'),
      api.post_process(post_process.DropExpectation),
  )
