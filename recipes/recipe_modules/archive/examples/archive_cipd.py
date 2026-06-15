# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import re

from recipe_engine import post_process

from PB.recipe_modules.build.archive import properties

from RECIPE_MODULES.build import chromium_tests_builder_config as ctbc

DEPS = [
    'archive',
    'chromium',
    'chromium_tests',
    'chromium_tests_builder_config',
    'depot_tools/bot_update',
    'recipe_engine/path',
    'recipe_engine/properties',
]

source_side_spec_path = ['archive', 'foo.json']
non_existing_spec_path = ['non', 'existing', 'foo.json']


def RunSteps(api):
  _, builder_config = api.chromium_tests_builder_config.lookup_builder()
  api.chromium_tests.configure_build(builder_config)
  update_properties = api.properties.get('update_properties')
  custom_vars = api.properties.get('custom_vars')

  update_result = api.bot_update.ensure_checkout()
  checkout_dir = update_result.checkout_dir
  source_dir = update_result.source_root.path
  build_dir = api.chromium.default_build_dir(source_dir)

  # Calling generic_archive_after_tests without generic_archive first
  # should result in a no-op.
  api.archive.generic_archive_after_tests(
      checkout_dir=checkout_dir,
      source_dir=source_dir,
      build_dir=build_dir,
      test_success=False)

  api.path.mock_add_paths(
      update_result.checkout_dir.joinpath(*source_side_spec_path))

  upload_results = api.archive.generic_archive(
      checkout_dir=checkout_dir,
      source_dir=source_dir,
      build_dir=build_dir,
      update_properties=update_properties,
      custom_vars=custom_vars,
      report_artifacts=True)
  api.archive.generic_archive_after_tests(
      checkout_dir=checkout_dir,
      source_dir=source_dir,
      build_dir=build_dir,
      upload_results=upload_results,
      test_success=True)


def _cipd_archive_data():
  cipd_archive_data = properties.CIPDArchiveData()
  cipd_archive_data.yaml_files.extend(['foo'])
  cipd_archive_data.refs.extend(['{%channel%}'])
  cipd_archive_data.tags['version'] = '{%chrome_version%}'
  cipd_archive_data.pkg_vars['targetarch'] = '{%arch%}'
  cipd_archive_data.compression.compression_level = 8
  return cipd_archive_data


def _input_properties():
  input_properties = properties.InputProperties()
  input_properties.cipd_archive_datas.extend([_cipd_archive_data()])
  return input_properties


def GenTests(api):
  ctbc_api = api.chromium_tests_builder_config

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
          **{'$build/archive': _input_properties()}),
      api.chromium.override_version(
          major=91, step_name='Generic Archiving Steps.get version'),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              'cipd',
              'pkg-register',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-ref',
              'canary',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-tag',
              'version:1.2.3.4',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.build foo",
          [
              '-pkg-var',
              'targetarch:arm64',
          ],
      ),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'fuchsia_cipd_archive_amd64',
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
                      'TARGET_ARCH': 'intel',
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
          **{'$build/archive': _input_properties()}),
      api.chromium.override_version(
          major=91, step_name='Generic Archiving Steps.get version'),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-ref',
              'canary',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-tag',
              'version:1.2.3.4',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.build foo",
          [
              '-pkg-var',
              'targetarch:amd64',
          ],
      ),
      api.post_process(post_process.DropExpectation),
  )

  input_properties = _input_properties()
  input_properties.source_side_spec_path.extend(non_existing_spec_path)
  yield api.test(
      'non_existing_source_side_spec_path',
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
      api.post_process(post_process.StepException,
                       'Could not find specified archive config'),
      api.post_process(post_process.DropExpectation),
      status='INFRA_FAILURE',
  )

  input_properties = _input_properties()
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
          "Generic Archiving Steps.register foo",
          [
              '-ref',
              'legacy88',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-tag',
              'version:2.3.4.5',
          ],
      ),
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
              re.compile('.*/path/to/another/file\\.txt'),
              'gs://any-bucket/dest_dir/path/to/another/file.txt'
          ]),
      api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  cipd_archive_data = _cipd_archive_data()
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
                  android_config='base_config',
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
          "Generic Archiving Steps.register foo",
          [
              '-tag',
              'version:1.2.3.4',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.build foo",
          [
              '-pkg-var',
              'targetarch:arm32',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps After Tests.cipd set-ref foo",
          ['-version', '40-chars-fake-of-the-package-instance_id']),
      api.post_process(post_process.StepCommandContains,
                       "Generic Archiving Steps After Tests.cipd set-ref foo",
                       ['-ref', 'stable']),
      api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  cipd_archive_data = _cipd_archive_data()
  cipd_archive_data.refs.extend(['{%milestone%}'])
  input_properties.cipd_archive_datas.extend([cipd_archive_data])
  yield api.test(
      'extend_milestone',
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
      api.post_process(post_process.MustRun,
                       "Generic Archiving Steps.register foo"),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-ref',
              'canary',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-ref',
              '91',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-tag',
              'version:1.2.3.4',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.build foo",
          [
              '-pkg-var',
              'targetarch:arm64',
          ],
      ),
      api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  cipd_archive_data = _cipd_archive_data()
  cipd_archive_data.refs.extend(
      ['{% m{%milestone%}_fuchsia_ready when is_canary %}'])
  cipd_archive_data.tags[
      'canary-milestone'] = '{% {%milestone%} when is_canary %}'
  input_properties.cipd_archive_datas.extend([cipd_archive_data])
  yield api.test(
      'is_canary_true',
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
      api.post_process(post_process.MustRun,
                       "Generic Archiving Steps.register foo"),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-ref',
              'canary',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-ref',
              'm91_fuchsia_ready',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-tag',
              'canary-milestone:91',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-tag',
              'version:1.2.3.4',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.build foo",
          [
              '-pkg-var',
              'targetarch:arm64',
          ],
      ),
      api.post_process(post_process.DropExpectation),
  )

  # Use the same input_properties as the previous test.
  yield api.test(
      'is_canary_false',
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
          major=90, step_name='Generic Archiving Steps.get version'),
      api.post_process(post_process.MustRun,
                       "Generic Archiving Steps.register foo"),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-ref',
              'beta',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.register foo",
          [
              '-tag',
              'version:1.2.3.4',
          ],
      ),
      api.post_process(
          post_process.StepCommandContains,
          "Generic Archiving Steps.build foo",
          [
              '-pkg-var',
              'targetarch:arm64',
          ],
      ),
      api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  cipd_archive_data = _cipd_archive_data()
  cipd_archive_data.refs.extend(
      ['{% m{%milestone%}_fuchsia_ready when unknown condition %}'])
  input_properties.cipd_archive_datas.extend([cipd_archive_data])
  yield api.test(
      'unknown_condition',
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
      api.post_process(post_process.StepFailure,
                       "Generic Archiving Steps.Unknown condition"),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )
