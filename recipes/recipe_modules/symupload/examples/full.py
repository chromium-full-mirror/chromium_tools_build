# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import base64
from recipe_engine import post_process

from PB.recipe_modules.build.symupload import properties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, chromium_checkout, symupload
from RECIPE_MODULES.recipe_engine import (
    file,
    path,
    platform,
    properties as properties_module,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  chromium_checkout: chromium_checkout.API
  file: file.API
  path: path.API
  platform: platform.API
  properties: properties_module.API
  symupload: symupload.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  file: file.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties_module.TEST_API
  symupload: symupload.TEST_API


def RunSteps(api: DEPS):
  api.chromium_checkout.set_paths(api.path.cleanup_dir, 'fake-repo')

  api.chromium.set_config(
      'chromium', **{
          'TARGET_PLATFORM': api.properties.get('target_platform'),
          'HOST_PLATFORM': api.properties.get('host_platform')
      })

  api.symupload(
      api.path.tmp_base_dir,
      experimental=api.properties.get('experimental'),
      custom_vars=api.properties.get('custom_vars'))


def GenTests(api: TEST_DEPS):
  input_properties = properties.InputProperties()
  symupload_data = input_properties.symupload_datas.add()

  symupload_data.artifact = 'some_artifact.txt'
  symupload_data.url = 'https://some.url.com'
  symupload_data.file_globs.append('glob*.txt')

  yield api.test(
      'basic_win',
      api.platform('win', 64),
      api.properties(target_platform='win', host_platform='win'),
      api.path.exists(api.path.tmp_base_dir / 'symupload.exe'),
      api.symupload(input_properties),
  )

  yield api.test(
      'basic_win_override_win_toolchain_json',
      api.platform('win', 64),
      api.properties(target_platform='win', host_platform='win'),
      api.override_step_data(
          'symupload.find_win_toolchain',
          api.file.read_json({
              'path':
                  'C:\\src\\chromium\\src\\win_toolchain\\20d5f2553f',
              'runtime_dirs': [
                  'C:\\src\\chromium\\src\\win_toolchain\\20d5f2553f\\sys64',
                  'C:\\src\\chromium\\src\\win_toolchain\\20d5f2553f\\sys32',
              ],
          })),
      api.path.exists(api.path.tmp_base_dir / 'symupload.exe'),
      api.symupload(input_properties),
  )

  yield api.test(
      'basic_linux/mac',
      api.platform('mac', 64),
      api.properties(target_platform='mac', host_platform='mac'),
      api.path.exists(api.path.tmp_base_dir / 'symupload'),
      api.symupload(input_properties),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no symupload binary',
      api.platform('win', 64),
      api.properties(target_platform='win', host_platform='win'),
      api.symupload(input_properties),
      api.post_process(post_process.StepException, 'symupload'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'no action',
      api.platform('mac', 64),
      api.properties(target_platform='mac', host_platform='mac'),
      api.symupload(properties.InputProperties()),
      api.post_process(post_process.DoesNotRun, 'symupload'),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'experimental',
      api.properties(
          target_platform='linux', host_platform='linux', experimental=True),
      api.path.exists(api.path.tmp_base_dir / 'symupload'),
      api.symupload(input_properties),
      api.post_process(post_process.DoesNotRun, 'symupload.symupload'),
      api.post_process(post_process.DoesNotRun, 'symupload.symupload_v2'),
      api.post_process(post_process.DropExpectation),
  )

  encoded_api_key = base64.b64encode(b'encrypted_api_key')

  input_properties_v2 = properties.InputProperties()
  symupload_data = input_properties_v2.symupload_datas.add()
  symupload_data.artifacts.append('some_artifact.txt')
  symupload_data.url = 'https://some.url.com'
  symupload_data.file_globs.append('glob*.txt')
  symupload_data.base64_api_key = encoded_api_key
  symupload_data.kms_key_path = "some/path"

  yield api.test(
      'win_symupload_v2',
      api.platform('win', 64),
      api.properties(target_platform='win', host_platform='win'),
      api.path.exists(api.path.tmp_base_dir / 'symupload.exe'),
      api.symupload(input_properties_v2),
      api.post_process(post_process.MustRun, 'symupload.symupload_v2'),
      api.post_process(post_process.StepCommandContains,
                       'symupload.symupload_v2', [
                           '--api-key-file',
                           '[CLEANUP]\\symupload-api-key.txt',
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux/mac_symupload_v2',
      api.properties(target_platform='linux', host_platform='linux'),
      api.path.exists(api.path.tmp_base_dir / 'symupload'),
      api.symupload(input_properties_v2),
      api.post_process(post_process.MustRun, 'symupload.symupload_v2'),
      api.post_process(post_process.StepCommandContains,
                       'symupload.symupload_v2', [
                           '--api-key-file',
                           '[CLEANUP]/symupload-api-key.txt',
                       ]),
  )

  symupload_data.artifact_type = "dsym"
  yield api.test(
      'mac_symupload_v2',
      api.platform('mac', 64),
      api.properties(target_platform='mac', host_platform='mac'),
      api.path.exists(api.path.tmp_base_dir / 'symupload'),
      api.symupload(input_properties_v2),
      api.post_process(post_process.MustRun, 'symupload.symupload_v2'),
      api.post_process(post_process.StepCommandContains,
                       'symupload.symupload_v2', [
                           '--artifact_type',
                           'dsym',
                       ]),
  )

  yield api.test(
      'check_file_glob_abs_path',
      api.properties(target_platform='linux', host_platform='linux'),
      api.path.exists(api.path.tmp_base_dir / 'symupload'),
      api.symupload(input_properties_v2),
      api.post_process(post_process.StepCommandContains,
                       'symupload.symupload_v2', [
                           '--artifacts',
                           '[TMP_BASE]/glob1.txt,[TMP_BASE]/glob2.txt,'
                           '[TMP_BASE]/some_artifact.txt',
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'retry_symupload_v2_failure',
      api.properties(target_platform='linux', host_platform='linux'),
      api.path.exists(api.path.tmp_base_dir / 'symupload'),
      api.symupload(input_properties_v2),
      api.step_data('symupload.symupload_v2', retcode=1),
      # Check if there is a second run
      api.post_process(post_process.StepCommandContains,
                       'symupload.symupload_v2 (2)', []),
      api.step_data('symupload.symupload_v2 (2)', retcode=1),
      api.step_data('symupload.symupload_v2 (3)', retcode=1),
      api.post_process(post_process.StepException, 'symupload'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('INFRA_FAILURE'),
  )

  yield api.test(
      'retry_symupload_v2_success',
      api.properties(target_platform='linux', host_platform='linux'),
      api.path.exists(api.path.tmp_base_dir / 'symupload'),
      api.symupload(input_properties_v2),
      api.step_data('symupload.symupload_v2', retcode=1),
      api.step_data('symupload.symupload_v2 (2)', retcode=1),
      api.post_process(post_process.StepSuccess, 'symupload'),
      api.post_process(post_process.DropExpectation),
  )

  input_properties_v2 = properties.InputProperties()
  symupload_data = input_properties_v2.symupload_datas.add()
  symupload_data.artifacts.append('some_artifact.txt')
  symupload_data.url = 'https://some.url.com'
  symupload_data.file_globs.append('glob*.txt')
  symupload_data.base64_api_key = encoded_api_key

  yield api.test(
      'linux/mac_symupload_v2_missing_kms_key',
      api.properties(target_platform='linux', host_platform='linux'),
      api.path.exists(api.path.tmp_base_dir / 'symupload'),
      api.symupload(input_properties_v2),
      api.post_process(post_process.StepException, 'symupload'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )

  input_properties_file = properties.InputProperties(source_side_spec_path=[
      'src-internal',
      'infra',
      'official_configs',
      'bling',
      'symupload_configs.json',
  ])

  yield api.test(
      'linux_symupload_v2_config_file',
      api.properties(target_platform='linux', host_platform='linux'),
      api.path.exists(
          api.path.tmp_base_dir / 'symupload',
          api.path.cleanup_dir.joinpath('src-internal', 'infra',
                                        'official_configs', 'bling',
                                        'symupload_configs.json')),
      api.symupload(input_properties_file),
      api.post_process(post_process.MustRun, 'symupload.symupload_v2'),
      api.post_process(post_process.StepCommandContains,
                       'symupload.symupload_v2', [
                           '--artifacts',
                           '[TMP_BASE]/some_artifact.txt',
                           '--api-key-file',
                           '[CLEANUP]/symupload-api-key.txt',
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  input_properties_v2 = properties.InputProperties()
  symupload_data = input_properties_v2.symupload_datas.add()
  symupload_data.artifacts.append('some_artifact.txt')
  symupload_data.url = '{%url%}'
  symupload_data.file_globs.append('glob*.txt')
  symupload_data.base64_api_key = base64.b64encode(b'foo_key')
  symupload_data.kms_key_path = '{%kms_key_path%}/{%kms_key_basename%}'

  yield api.test(
      'symupload_with_custom_vars',
      api.platform('win', 64),
      api.properties(
          target_platform='win',
          host_platform='win',
          custom_vars={
              'url': 'https://foo.com',
              'base64_api_key': encoded_api_key,
              'kms_key_path': 'some/path',
              'kms_key_basename': 'bar'
          }),
      api.path.exists(api.path.tmp_base_dir / 'symupload.exe'),
      api.symupload(input_properties_v2),
      api.post_process(post_process.StepCommandContains,
                       'symupload.write encrypted api key', [
                           "copy",
                           "encrypted_api_key",
                       ]),
      api.post_process(post_process.StepCommandContains,
                       'symupload.Prepare API key.decrypt', [
                           "some/path/bar",
                       ]),
      api.post_process(post_process.StepCommandContains,
                       'symupload.symupload_v2', [
                           "--server-urls",
                           "https://foo.com",
                       ]),
      api.post_process(post_process.DropExpectation),
  )

  input_properties_v2 = properties.InputProperties()
  symupload_data = input_properties_v2.symupload_datas.add()
  symupload_data.artifacts.append('some_artifact.txt')
  symupload_data.url = '{%url%}'
  symupload_data.file_globs.append('glob*.txt')
  symupload_data.base64_api_key = base64.b64encode(b'foo_key')
  symupload_data.kms_key_path = '{%bad_placeholder%}'

  yield api.test(
      'symupload_with_unresolved_placeholder',
      api.platform('win', 64),
      api.properties(
          target_platform='win',
          host_platform='win',
          custom_vars={
              'url': 'https://foo.com',
              'base64_api_key': encoded_api_key,
              'kms_key_path': 'some/path',
          }),
      api.path.exists(api.path.tmp_base_dir / 'symupload.exe'),
      api.symupload(input_properties_v2),
      api.post_process(post_process.MustRun,
                       'symupload.Unresolved placeholder'),
      api.expect_status('INFRA_FAILURE'),
      api.post_process(post_process.DropExpectation),
  )
