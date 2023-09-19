# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from PB.recipe_modules.build.archive import properties

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'archive',
    'chromium',
    'chromium_checkout',
    'chromium_tests',
    'chromium_tests_builder_config',
    'recipe_engine/path',
    'recipe_engine/properties',
]

source_side_spec_path = ['archive', 'foo.json']
non_existing_spec_path = ['non', 'existing', 'foo.json']


def RunSteps(api):
  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  api.chromium_tests.configure_build(builder_config)
  build_dir = api.chromium.output_dir
  update_properties = api.properties.get('update_properties')
  custom_vars = api.properties.get('custom_vars')

  # Calling generic_archive_after_tests without generic_archive first
  # should result in a no-op.
  api.archive.generic_archive_after_tests(
      build_dir=build_dir, test_success=False)

  api.path.mock_add_paths(
      api.chromium_checkout.checkout_dir.join(*source_side_spec_path))

  upload_results = api.archive.generic_archive(
      build_dir=build_dir,
      update_properties=update_properties,
      custom_vars=custom_vars,
      report_artifacts=True)
  api.archive.generic_archive_after_tests(
      build_dir=build_dir, upload_results=upload_results, test_success=True)


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

  input_properties = properties.InputProperties()
  cipd_archive_data = properties.CIPDArchiveData()
  cipd_archive_data.yaml_files.extend(['foo'])
  cipd_archive_data.refs.extend(['{%channel%}'])
  cipd_archive_data.tags['version'] = '{%chrome_version%}'
  cipd_archive_data.pkg_vars['targetarch'] = '{%arch%}'
  cipd_archive_data.compression.compression_level = 8
  input_properties.cipd_archive_datas.extend([cipd_archive_data])

  yield api.test(
      'fuchsia_cipd_archive_arm64',
      api.chromium.generic_build(
          builder_group='fake-group', builder='fake-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  chromium_config_kwargs={
                      'TARGET_ARCH': 'arm',
                      'TARGET_BITS': 64,
                      'TARGET_PLATFORM': 'fuchsia',
                  },
              ),
          ).assemble()),
      api.properties(
          cipd_archive=True,
          update_properties={},
          custom_vars={
              'chrome_version': '1.2.3.4',
          },
          **{'$build/archive': input_properties}),
      api.chromium.override_version(
          major=91, step_name='Generic Archiving Steps.get version'),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.create foo", [
              'cipd', 'create', '-pkg-def', 'None/out/Release/foo',
              '-hash-algo', 'sha256', '-ref', 'canary', '-tag',
              'version:1.2.3.4', '-pkg-var', 'targetarch:arm64',
              '-compression-level', '8', '-json-output', '/path/to/tmp/json'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  input_properties.source_side_spec_path.extend(non_existing_spec_path)
  yield api.test(
      'fuchsia_cipd_archive_x64',
      api.chromium.generic_build(
          builder_group='fake-group', builder='fake-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  chromium_config_kwargs={
                      'TARGET_BITS': 64,
                      'TARGET_PLATFORM': 'fuchsia',
                  },
              ),
          ).assemble()),
      api.properties(
          cipd_archive=True,
          update_properties={},
          custom_vars={
              'chrome_version': '1.2.3.4',
          },
          **{'$build/archive': input_properties}),
      api.chromium.override_version(
          major=90, step_name='Generic Archiving Steps.get version'),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.create foo", [
              'cipd', 'create', '-pkg-def', 'None/out/Release/foo',
              '-hash-algo', 'sha256', '-ref', 'beta', '-tag', 'version:1.2.3.4',
              '-pkg-var', 'targetarch:amd64', '-compression-level', '8',
              '-json-output', '/path/to/tmp/json'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  for path in non_existing_spec_path:
    input_properties.source_side_spec_path.remove(path)
  input_properties.source_side_spec_path.extend(source_side_spec_path)
  yield api.test(
      'source_side_cipd_archive_data',
      api.chromium.generic_build(
          builder_group='fake-group', builder='fake-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  chromium_config_kwargs={
                      'TARGET_BITS': 64,
                      'TARGET_PLATFORM': 'fuchsia',
                  },
              ),
          ).assemble()),
      api.properties(
          cipd_archive=True,
          update_properties={},
          custom_vars={
              'chrome_version': '1.2.3.4',
          },
          **{'$build/archive': input_properties}),
      api.chromium.override_version(
          major=88, step_name='Generic Archiving Steps.get version'),
      api.archive._read_source_side_archive_spec(
          source_side_spec_path[-1], {
              "cipd_archive_datas": [{
                  "yaml_files": ["foo",],
                  "refs": ["{%channel%}",],
                  "tags": {
                      "version": "2.3.4.5",
                  },
              },],
          }),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.create foo", [
              'cipd', 'create', '-pkg-def', 'None/out/Release/foo',
              '-hash-algo', 'sha256', '-ref', 'legacy88', '-tag',
              'version:2.3.4.5', '-json-output', '/path/to/tmp/json'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'source_side_archive_data',
      api.chromium.generic_build(
          builder_group='fake-group', builder='fake-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  chromium_config='chromium',
                  chromium_config_kwargs={
                      'TARGET_BITS': 64,
                      'TARGET_PLATFORM': 'fuchsia',
                  },
              ),
          ).assemble()),
      api.properties(
          gcs_archive=True,
          update_properties={},
          **{'$build/archive': input_properties}),
      api.archive._read_source_side_archive_spec(
          source_side_spec_path[-1], {
              'archive_datas': [{
                  'files': ['/path/to/another/file.txt'],
                  'gcs_bucket': 'any-bucket',
                  'gcs_path': 'dest_dir/',
                  'archive_type': properties.ArchiveData.ARCHIVE_TYPE_FILES,
              },],
          }),
      api.post_process(
          post_process.StepCommandContains,
          'Generic Archiving Steps.gsutil upload '
          'dest_dir/path/to/another/file.txt', [
              'python3', '-u', 'RECIPE_MODULE[depot_tools::gsutil]/resources/'
              'gsutil_smart_retry.py', '--',
              'RECIPE_REPO[depot_tools]/gsutil.py', '----', 'cp',
              '/path/to/another/file.txt',
              'gs://any-bucket/dest_dir/path/to/another/file.txt'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  input_properties.cipd_archive_datas.remove(cipd_archive_data)
  cipd_archive_data.only_set_refs_on_tests_success = True
  cipd_archive_data.verification.verification_timeout = '5m'
  input_properties.cipd_archive_datas.extend([cipd_archive_data])
  yield api.test(
      'android_cipd_archive_arm32',
      api.chromium.generic_build(
          builder_group='fake-group', builder='fake-builder'),
      ctbc_api.properties(
          ctbc_api.properties_assembler_for_ci_builder(
              builder_group='fake-group',
              builder='fake-builder',
              builder_spec=ctbc.BuilderSpec.create(
                  gclient_config='chromium',
                  android_config='asan_symbolize',
                  chromium_config='clang_tot_android_asan',
                  chromium_config_kwargs={
                      'TARGET_BITS': 32,
                      'TARGET_ARCH': 'arm',
                  },
              ),
          ).assemble()),
      api.properties(
          cipd_archive=True,
          update_properties={},
          custom_vars={
              'chrome_version': '1.2.3.4',
          },
          **{'$build/archive': input_properties}),
      api.chromium.override_version(
          major=89, step_name='Generic Archiving Steps.get version'),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.create foo", [
              'cipd', 'create', '-pkg-def', 'None/out/Release/foo',
              '-hash-algo', 'sha256', '-tag', 'version:1.2.3.4', '-pkg-var',
              'targetarch:arm32', '-compression-level', '8',
              '-verification-timeout', '5m', '-json-output', '/path/to/tmp/json'
          ]),
      api.post_process(post_process.StepCommandContains,
                       "Generic Archiving Steps After Tests.cipd set-ref foo", [
                           'cipd', 'set-ref', 'foo', '-version',
                           '40-chars-fake-of-the-package-instance_id', '-ref',
                           'stable', '-json-output', '/path/to/tmp/json'
                       ]),
      api.post_process(post_process.DropExpectation),
  )
