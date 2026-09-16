# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import datetime

from PB.recipe_modules.build.archive import properties
from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import archive, chromium, squashfs
from RECIPE_MODULES.recipe_engine import (
  assertions,
  file,
  json,
  path,
  platform,
  properties as properties_module,
  runtime,
)


@dataclass
class DEPS(RecipeScriptApi):
  archive: archive.API
  assertions: assertions.API
  chromium: chromium.API
  file: file.API
  json: json.API
  path: path.API
  platform: platform.API
  properties: properties_module.API
  runtime: runtime.API
  squashfs: squashfs.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  archive: archive.TEST_API
  chromium: chromium.TEST_API
  file: file.TEST_API
  json: json.TEST_API
  platform: platform.TEST_API
  properties: properties_module.TEST_API
  runtime: runtime.TEST_API


TEST_CHROME_VERSION = '''MAJOR=91
MINOR=0
BUILD=4711
PATCH=0'''

TEST_HASH_MAIN = '5e3250aadda2b170692f8e762d43b7e8deadbeef'
TEST_COMMIT_POSITON_MAIN = 'refs/heads/B1@{#123456}'

TEST_HASH_COMPONENT = 'deadbeefdda2b170692f8e762d43b7e8e7a96686'
TEST_COMMIT_POSITON_COMPONENT = 'refs/heads/main@{#234}'

source_side_spec_path = ['archive', 'foo.json']


def RunSteps(api: DEPS):
  checkout_dir = api.path.cleanup_dir
  source_dir = checkout_dir / 'fake-repo'

  if 'test_get_channel_name' in api.properties:
    api.assertions.assertEqual(
      api.properties.get('channel'), api.archive.get_channel_name(source_dir)
    )
    return

  if 'test_get_milestone_position' in api.properties:
    api.assertions.assertEqual(
      api.properties.get('milestone_position'),
      api.archive._get_milestone_position(source_dir),
    )
    return

  if 'build_archive_url' in api.properties:
    api.archive.zip_and_upload_build(
      step_name='zip build',
      target=source_dir / 'Release/out',
      source_dir=source_dir,
    )
    return

  if 'gcs_archive' in api.properties:
    api.chromium.set_config('chromium')

    build_dir = api.m.path.mkdtemp()
    api.path.mock_add_paths(checkout_dir.joinpath(*source_side_spec_path))
    api.path.mock_add_paths(build_dir / 'existing-dir')
    api.path.mock_add_paths(build_dir / 'existing-file.json')
    api.path.mock_add_paths(
      api.path.start_dir.joinpath('squashfs', 'squashfs-tools', 'mksquashfs')
    )
    update_properties = api.properties.get('update_properties')
    custom_vars = api.properties.get('custom_vars')
    upload_results = api.archive.generic_archive(
      checkout_dir=checkout_dir,
      source_dir=source_dir,
      build_dir=build_dir,
      update_properties=update_properties,
      custom_vars=custom_vars,
      report_artifacts=True,
      use_hardlink=api.properties.get('use_hardlink', False),
      generate_sboms=True,
    )
    api.archive.generic_archive_after_tests(
      checkout_dir=checkout_dir,
      source_dir=source_dir,
      build_dir=build_dir,
      upload_results=upload_results,
      test_success=True,
    )
    return

  if 'no_llvm' not in api.properties:
    llvm_bin_dir = source_dir / 'third_party/llvm-build/Release+Asserts/bin'
    api.path.mock_add_paths(api.path.join(llvm_bin_dir, 'llvm-symbolizer'))
    api.path.mock_add_paths(api.path.join(llvm_bin_dir, 'sancov'))

    llvm_lib_dir = source_dir / 'third_party/llvm-build/Release+Asserts/lib'
    api.path.mock_add_paths(api.path.join(llvm_lib_dir, 'libstdc++.so.6'))

  build_dir = api.path.start_dir.joinpath('src', 'out', 'Release')

  common_kwargs = dict(
    source_dir=source_dir,
    update_properties=api.properties.get('update_properties'),
    gs_bucket='chromium',
    gs_acl=api.properties.get('gs_acl', ''),
    archive_path=api.properties.get('archive_path', 'path/to/archive'),
    primary_project=api.properties.get('primary_project'),
    sortkey_datetime=api.properties.get('sortkey_datetime', None),
  )

  if api.properties.get(
    'archive_schema_version', 0
  ) != 0 and not api.properties.get('paths_to_archive'):
    api.archive.clusterfuzz_archive_targets(
      **common_kwargs,
      archive_root=source_dir,
      archive_schema_version=api.properties.get('archive_schema_version', 0),
      build_dir=build_dir,
      compile_targets=api.properties.get('compile_targets', []),
    )
  else:
    api.archive.clusterfuzz_archive(
      **common_kwargs,
      archive_root=build_dir,
      paths_to_archive=api.properties.get('paths_to_archive', None),
    )


def GenTests(api: TEST_DEPS):
  update_properties = {
    'got_revision': TEST_HASH_MAIN,
    'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
  }
  for platform, build_files in (
    ('win', ['chrome', 'icu.dat', 'lib', 'file.obj']),
    ('mac', ['chrome', 'icu.dat', 'pdfsqueeze']),
    ('linux', ['chrome', 'icu.dat', 'lib.host']),
  ):
    yield api.test(
      'cf_archiving_%s' % platform,
      api.platform(platform, 64),
      api.properties(
        update_properties=update_properties,
        gs_acl='public-read',
        archive_subdir_suffix='subdir',
      ),
      api.override_step_data(
        'filter archive_root', api.json.output(build_files)
      ),
    )

  yield api.test(
    'cf_archiving_win64',
    api.platform('win', 64),
    api.properties(
      bitness=64,
      update_properties=update_properties,
      use_legacy=False,
    ),
    api.override_step_data('filter archive_root', api.json.output(['chrome'])),
  )

  yield api.test(
    'cf_archiving_win64_exp',
    api.platform('win', 64),
    api.properties(
      bitness=64,
      update_properties=update_properties,
      use_legacy=False,
    ),
    api.override_step_data('filter archive_root', api.json.output(['chrome'])),
    api.runtime(is_experimental=True),
  )

  yield api.test(
    'cf_archiving_verbatim',
    api.platform('linux', 64),
    api.properties(
      update_properties=update_properties,
      archive_path='bleep-bloop/foo-bar',
    ),
    api.post_process(post_process.DropExpectation),
  )

  # Verifies that clusterfuzz_archive fails with an AssertionError when
  # archive_path is empty.
  yield api.test(
    'cf_archiving_assert_failure',
    api.platform('linux', 64),
    api.properties(
      update_properties=update_properties,
      archive_path='',
    ),
    api.expect_exception('AssertionError'),
    api.post_process(post_process.DropExpectation),
  )

  # Overwrite the build config and ensure it is used in the GS archive name.
  def check_gs_url_equals(check, steps, expected):
    check('gsutil upload' in steps)
    check(expected == steps['gsutil upload'].cmd[-1])

  yield api.test(
    'custom_build_config',
    api.platform('linux', 64),
    api.properties(
      archive_path='linux-debease/chrome-asan-linux-debease',
      update_properties=update_properties,
    ),
    api.post_process(
      check_gs_url_equals,
      'gs://chromium/linux-debease/'
      'chrome-asan-linux-debease-refs_heads_B1-123456.zip',
    ),
    api.post_process(post_process.DropExpectation),
  )

  # A component build with git.
  update_properties = {
    'got_x10_revision': TEST_HASH_COMPONENT,
    'got_x10_revision_cp': TEST_COMMIT_POSITON_COMPONENT,
  }
  yield api.test(
    'cf_archiving_component',
    api.platform('linux', 64),
    api.properties(
      update_properties=update_properties,
      archive_path='linux-release/chrome-asan-linux-release-x10-component',
      primary_project='x10',
    ),
    api.override_step_data(
      'filter archive_root', api.json.output(['chrome', 'resources'])
    ),
  )

  update_properties = {
    'got_revision': TEST_HASH_MAIN,
    'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
  }
  yield api.test(
    'cf_archiving_no_llvm',
    api.platform('linux', 64),
    api.properties(
      update_properties=update_properties,
      no_llvm=True,
    ),
    api.override_step_data('filter archive_root', api.json.output(['chrome'])),
  )

  yield api.test(
    'zip_and_upload_custom_location',
    api.platform('linux', 64),
    api.properties(
      build_archive_url='gs://dummy-bucket/Linux Release/full-build.zip'
    ),
  )

  update_properties = {
    'got_revision': TEST_HASH_MAIN,
  }
  yield api.test(
    'cf_archiving_with_sortkey_datetime',
    api.platform('linux', 64),
    api.properties(
      update_properties=update_properties,
      gs_acl='public-read',
      archive_subdir_suffix='subdir',
      sortkey_datetime=datetime.datetime.utcfromtimestamp(100),
    ),
    api.override_step_data(
      'filter archive_root', api.json.output(['chrome', 'resources'])
    ),
  )

  yield api.test(
    'cf_archiving_with_paths_to_archive',
    api.platform('linux', 64),
    api.properties(
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      gs_acl='public-read',
      archive_subdir_suffix='subdir',
      paths_to_archive=['target1', 'dir/target2'],
    ),
    api.post_process(post_process.DoesNotRun, 'filter archive_root'),
    api.post_process(post_process.DoesNotRun, 'Copy llvm-symbolizer'),
    api.post_process(post_process.DoesNotRun, 'Copy sancov'),
    api.post_process(post_process.DoesNotRun, 'Copy libstdc++.so.6'),
    api.post_process(
      post_process.StepCommandContains,
      'zipping',
      ['["target1", "dir/target2"]'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  def check_stdin(check, step_odict, step, included_args, excluded_args=None):
    for included_arg in included_args:
      check(
        'stdin for step %s contained %s' % (step, included_arg),
        included_arg in step_odict[step].stdin,
      )
    excluded_args = excluded_args or []
    for excluded_arg in excluded_args:
      check(
        'stdin for step %s did not contain %s' % (step, excluded_arg),
        excluded_arg not in step_odict[step].stdin,
      )

  for archive_type, archive_filename, include_dirs in (
    (properties.ArchiveData.ARCHIVE_TYPE_UNSPECIFIED, 'any-path.zip', True),
    (properties.ArchiveData.ARCHIVE_TYPE_ZIP, 'any-path.zip', True),
    (properties.ArchiveData.ARCHIVE_TYPE_FILES, '', False),
    (properties.ArchiveData.ARCHIVE_TYPE_FLATTEN_FILES, '', False),
    (properties.ArchiveData.ARCHIVE_TYPE_TAR_GZ, 'any-path.tar.gz', True),
    (properties.ArchiveData.ARCHIVE_TYPE_TAR_ZSTD, 'any-path.tar.zst', True),
    (properties.ArchiveData.ARCHIVE_TYPE_SQUASHFS, 'any-path.squash', True),
    (properties.ArchiveData.ARCHIVE_TYPE_RECURSIVE, '', True),
  ):
    input_properties = properties.InputProperties()
    archive_data = properties.ArchiveData()
    archive_data.files.extend(
      [
        'folder1/chrome',
        'folder2/snapshot_blob.bin',
        'before_rename_file',
      ]
    )
    if include_dirs:
      archive_data.dirs.extend(
        [
          'directory1',
          'directory2',
          'path/to/directory3',
          'locales',
          'swiftshader',
        ]
      )
      rename_dir = properties.ArchiveDirRename()
      rename_dir.from_dir = "directory1"
      rename_dir.to_dir = "dir/one"
      archive_data.rename_dirs.extend([rename_dir])
      rename_partial_dir = properties.ArchiveDirRename()
      rename_partial_dir.from_dir = "path/to"
      rename_partial_dir.to_dir = "path_to"
      archive_data.rename_dirs.extend([rename_partial_dir])
      rename_root = properties.ArchiveDirRename()
      rename_root.from_dir = "."
      rename_root.to_dir = "archive_root"
      archive_data.rename_dirs.extend([rename_root])
    rename_file = properties.ArchiveFileRename()
    rename_file.from_file = "before_rename_file"
    rename_file.to_file = "after_rename_file_{%timestamp%}"
    archive_data.rename_files.extend([rename_file])
    # archive_data.rename_dirs should be able to modify any dir, not just those
    # added by archive_data.dirs.
    rename_file_dir = properties.ArchiveDirRename()
    rename_file_dir.from_dir = "folder1"
    rename_file_dir.to_dir = "folder_one"
    archive_data.rename_dirs.extend([rename_file_dir])
    archive_data.file_globs.append('glob*.txt')
    archive_data.gcs_bucket = 'any-bucket'
    archive_data.gcs_path = (
      '{%position%}/{%commit%}/{%timestamp%}/'
      '{%chromium_version%}' + archive_filename
    )
    archive_data.archive_type = archive_type
    archive_data.root_permission_override = "755"
    input_properties.archive_datas.extend([archive_data])

    def add_directory_checks():
      post_tests = []
      if include_dirs:
        post_tests.append(
          api.post_process(
            post_process.StepCommandContains,
            "Generic Archiving Steps.Copy folder directory1",
            [
              "copytree",
              "--symlinks",
              "--allow-override",
              "[CLEANUP]/tmp_tmp_1/directory1",
              "[CLEANUP]/tmp_tmp_2/directory1",
            ],
          )
        )
        post_tests.append(
          api.post_process(
            post_process.StepCommandContains,
            "Generic Archiving Steps.Copy folder directory2",
            [
              "copytree",
              "--symlinks",
              "--allow-override",
              "[CLEANUP]/tmp_tmp_1/directory2",
              "[CLEANUP]/tmp_tmp_2/directory2",
            ],
          )
        )
        post_tests.append(
          api.post_process(
            post_process.StepCommandContains,
            "Generic Archiving Steps.Copy folder path/to/directory3",
            [
              "copytree",
              "--symlinks",
              "--allow-override",
              "[CLEANUP]/tmp_tmp_1/path/to/directory3",
              "[CLEANUP]/tmp_tmp_2/path/to/directory3",
            ],
          )
        )
        post_tests.append(
          api.post_process(
            post_process.StepCommandContains,
            "Generic Archiving Steps.Move dir: 'directory1'->'dir/one'",
            [
              "move",
              "[CLEANUP]/tmp_tmp_2/directory1",
              "[CLEANUP]/tmp_tmp_2/dir/one",
            ],
          )
        )
        post_tests.append(
          api.post_process(
            post_process.StepCommandContains,
            "Generic Archiving Steps.Move dir: 'path/to'->'path_to'",
            [
              "move",
              "[CLEANUP]/tmp_tmp_2/path/to",
              "[CLEANUP]/tmp_tmp_2/path_to",
            ],
          )
        )
        post_tests.append(
          api.post_process(
            post_process.StepCommandContains,
            "Generic Archiving Steps.Move dir: '.'->'archive_root'",
            [
              # NOTE: A "root move" involves moving the original archive
              # dir to a temp dir before the final move, thus the new
              # 'tmp_tmp_3' in the path.
              "move",
              "[CLEANUP]/tmp_tmp_3/tmp_tmp_2",
              "[CLEANUP]/tmp_tmp_2/archive_root",
            ],
          )
        )
      return sum(post_tests, api.empty_test_data())

    def add_naming_checks(archive_type):
      post_tests = []
      # Verify that renamed files/dirs are referenced by their new names in
      # relevant archiving steps.
      if archive_type in [
        properties.ArchiveData.ARCHIVE_TYPE_UNSPECIFIED,
        properties.ArchiveData.ARCHIVE_TYPE_ZIP,
      ]:
        post_tests.append(
          api.post_process(
            check_stdin,
            "Generic Archiving Steps.Create generic archive",
            [
              "archive_root/after_rename_file_20120514125323",
              "[CLEANUP]/tmp_tmp_2/archive_root/folder_one/chrome",
              "[CLEANUP]/tmp_tmp_2/archive_root/dir/one",
              "[CLEANUP]/tmp_tmp_2/archive_root/path_to/directory3",
            ],
          )
        )
      if archive_type in [
        properties.ArchiveData.ARCHIVE_TYPE_FILES,
        properties.ArchiveData.ARCHIVE_TYPE_FLATTEN_FILES,
      ]:
        post_tests.append(
          api.post_process(
            post_process.StepCommandContains,
            "Generic Archiving Steps.gsutil upload 123456/5e3250aadda2b17"
            "0692f8e762d43b7e8deadbeef/20120514125321/51.0.2704.0/"
            "after_rename_file_20120514125323",
            [
              "cp",
              "[CLEANUP]/tmp_tmp_2/after_rename_file_20120514125323",
            ],
          )
        )
      if archive_type in [
        properties.ArchiveData.ARCHIVE_TYPE_TAR_GZ,
      ]:
        post_tests.append(
          api.post_process(
            check_stdin,
            "Generic Archiving Steps.Create tar.gz archive",
            [
              "archive_root/after_rename_file_20120514125323",
              "[CLEANUP]/tmp_tmp_2/archive_root/folder_one/chrome",
              "[CLEANUP]/tmp_tmp_2/archive_root/dir/one",
              "[CLEANUP]/tmp_tmp_2/archive_root/path_to/directory3",
            ],
          )
        )
      if archive_type in [
        properties.ArchiveData.ARCHIVE_TYPE_RECURSIVE,
      ]:
        post_tests.append(
          api.post_process(
            post_process.StepCommandContains,
            "Generic Archiving Steps.gsutil upload 123456/5e3250aadda2b"
            "170692f8e762d43b7e8deadbeef/20120514125321/51.0.2704.0",
            [
              "cp",
              "-R",
              "[CLEANUP]/tmp_tmp_2/archive_root/dir/one",
            ],
          )
        )
      return sum(post_tests, api.empty_test_data())

    yield api.test(
      'generic_archive_{}'.format(archive_type),
      api.properties(
        gcs_archive=True,
        update_properties={
          'got_revision': TEST_HASH_MAIN,
          'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
        },
        **{'$build/archive': input_properties},
      ),
      api.post_process(
        post_process.StepCommandContains,
        "Generic Archiving Steps.Move file",
        [
          "move",
          "[CLEANUP]/tmp_tmp_2/before_rename_file",
          "[CLEANUP]/tmp_tmp_2/after_rename_file_20120514125323",
        ],
      ),
      api.post_process(
        post_process.StepCommandContains,
        "Generic Archiving Steps.Update temporary folder permissions",
        ["chmod", "755", "[CLEANUP]/tmp_tmp_2"],
      ),
      api.post_process(
        post_process.StepCommandContains,
        "Generic Archiving Steps.Move dir: 'folder1'->'folder_one'",
        [
          "move",
          "[CLEANUP]/tmp_tmp_2/folder1",
          "[CLEANUP]/tmp_tmp_2/folder_one",
        ],
      ),
      add_directory_checks(),
      add_naming_checks(archive_type),
      api.post_process(post_process.DropExpectation),
    )

    yield api.test(
      'generic_archive_missing_got_revision_cp_{}'.format(archive_type),
      api.properties(
        gcs_archive=True,
        update_properties={
          'got_revision': TEST_HASH_MAIN,
        },
        **{'$build/archive': input_properties},
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
    )

    yield api.test(
      f'generic_archive_batch_copy_{archive_type}',
      api.properties(
        gcs_archive=True,
        update_properties={
          'got_revision': TEST_HASH_MAIN,
          'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
        },
        use_hardlink=True,
        **{'$build/archive': input_properties},
      ),
      api.post_process(
        post_process.LogContains,
        'Generic Archiving Steps.Write file list to copy',
        'tmp_tmp_3',
        [
          'folder1/chrome',
          'folder2/snapshot_blob.bin',
          'before_rename_file',
          'glob1.txt',
          'glob2.txt',
        ],
      ),
      api.post_process(post_process.DropExpectation),
    )

    yield api.test(
      'generic_archive_missing_got_revision_{}'.format(archive_type),
      api.properties(
        gcs_archive=True,
        update_properties={
          'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
        },
        **{'$build/archive': input_properties},
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
    )

  yield api.test(
    'generic_archive_nothing_to_archive',
    api.properties(
      gcs_archive=True, update_properties={}, **{'$build/archive': {}}
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'x86/{%position%}_{%commit%}_{%timestamp%}/chrome'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  archive_data.latest_upload.gcs_path = "x86/latest/latest.txt"
  archive_data.latest_upload.gcs_file_content = (
    '{%position%}_{%commit%}_{%timestamp%}'
  )
  archive_data.only_upload_on_tests_success = True
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_with_update_latest',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps After Tests.Write latest file',
      ['123456_5e3250aadda2b170692f8e762d43b7e8deadbeef_20120514125323'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['missing-dir', 'existing-dir'])
  archive_data.files.extend(['missing-file.json', 'existing-file.json'])
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  archive_data.skip_empty_source = True
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_skip_empty_sources',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(
      post_process.MustRun,
      'Generic Archiving Steps.Copy file existing-file.json',
    ),
    api.post_process(
      post_process.MustRun, 'Generic Archiving Steps.Copy folder existing-dir'
    ),
    api.post_process(
      post_process.DoesNotRun,
      'Generic Archiving Steps.Copy file missing-file.json',
    ),
    api.post_process(
      post_process.DoesNotRun, 'Generic Archiving Steps.Copy folder missing-dir'
    ),
    api.post_process(
      check_stdin,
      'Generic Archiving Steps.Create generic archive',
      [
        '[CLEANUP]/tmp_tmp_2/existing-file.json',
        '[CLEANUP]/tmp_tmp_2/existing-dir',
      ],
      [
        '[CLEANUP]/tmp_tmp_2/missing-file.json',
        '[CLEANUP]/tmp_tmp_2/missing-dir',
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.files.extend(['missing-file.json', 'existing-file.json'])
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_fail_missing_files',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(
      post_process.StepFailure, 'Generic Archiving Steps.Validate files'
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties.verify_paths_only = True

  yield api.test(
    'verify_archive_paths_fail_missing_files',
    api.properties(gcs_archive=True, **{'$build/archive': input_properties}),
    api.post_process(
      post_process.StepFailure, 'Verify Archive Paths.Validate files'
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  input_properties.source_side_spec_path.extend(source_side_spec_path)
  input_properties.verify_paths_only = True

  yield api.test(
    'verify_paths_only_should_copy_to_source_side_archive_spec',
    api.properties(gcs_archive=True, **{'$build/archive': input_properties}),
    api.archive._read_source_side_archive_spec(
      source_side_spec_path[-1],
      {
        "archive_datas": [
          {
            "files": [
              "missing-file.json",
            ],
          },
        ],
      },
    ),
    api.post_process(
      post_process.StepFailure, 'Verify Archive Paths.Validate files'
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['missing-dir', 'existing-dir'])
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_fail_missing_dirs',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(
      post_process.StepFailure, 'Generic Archiving Steps.Validate directories'
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties.verify_paths_only = True

  yield api.test(
    'verify_archive_paths_fail_missing_dirs',
    api.properties(gcs_archive=True, **{'$build/archive': input_properties}),
    api.post_process(
      post_process.StepFailure, 'Verify Archive Paths.Validate directories'
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.file_globs.extend(['missing-file.json', '*-file.json'])
  input_properties.archive_datas.extend([archive_data])
  input_properties.verify_paths_only = True

  yield api.test(
    'verify_archive_paths_missing_glob_is_allowed',
    api.properties(gcs_archive=True, **{'$build/archive': input_properties}),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.files.extend(['base-dir-file.json'])
  archive_data.base_dir = 'base_dir'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  input_properties.archive_datas.extend([archive_data])
  input_properties.verify_paths_only = True

  yield api.test(
    'verify_archive_paths_with_base_dir',
    api.properties(gcs_archive=True, **{'$build/archive': input_properties}),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  input_properties.archive_datas.extend([properties.ArchiveData()])
  input_properties.cipd_archive_datas.extend([properties.CIPDArchiveData()])
  input_properties.verify_paths_only = True

  yield api.test(
    'verify_archive_paths_should_fail_with_cipd_archive_datas',
    api.properties(gcs_archive=True, **{'$build/archive': input_properties}),
    api.post_check(post_process.StepFailure, 'Verify Archive Paths'),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.files.extend(['missing-file.json'])
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  archive_data.skip_empty_source = True
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_pass_zip_with_empty_source',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(
      post_process.DoesNotRun,
      'Generic Archiving Steps.Copy file missing-file.json',
    ),
    api.step_data('Generic Archiving Steps.Create generic archive', retcode=1),
    api.post_process(
      post_process.DoesNotRun, 'Generic Archiving Steps.gsutil upload '
    ),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.files.extend(['missing-file.json'])
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_TAR_GZ
  archive_data.skip_empty_source = True
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_pass_tar_gz_with_empty_source',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(
      post_process.DoesNotRun,
      'Generic Archiving Steps.Copy file missing-file.json',
    ),
    api.step_data('Generic Archiving Steps.Create tar.gz archive', retcode=1),
    api.post_process(
      post_process.DoesNotRun, 'Generic Archiving Steps.gsutil upload '
    ),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = (
    'x86/{%position%}_{%commit%}_{%timestamp%}_'
    '{%builder_name%}_{%build_number%}/chrome'
  )
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  archive_data.latest_upload.gcs_path = "x86/latest/latest.txt"
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_no_latest_gcs_content',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      **{'$build/archive': input_properties},
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'x86/{%position%}/chrome'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  archive_data.revisions_file.gcs_path = 'x86/{%position%}/REVISIONS'
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_with_revisions_file',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
        'got_v8_revision': '466dd2d77f6dd56a9174d7389e788cb7367d818d',
        'got_v8_revision_cp': 'refs/heads/9.7.48@{#1}',
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Write REVISIONS file',
      [
        '{"chromium_revision": "123456", "got_revision": "5e3250a'
        'adda2b170692f8e762d43b7e8deadbeef", "got_revision_cp": '
        '"refs/heads/B1@{#123456}", "got_v8_revision": "466dd2d77f6'
        'dd56a9174d7389e788cb7367d818d", "got_v8_revision_cp": "refs'
        '/heads/9.7.48@{#1}", "v8_revision": "1", "v8_revision_'
        'git": "466dd2d77f6dd56a9174d7389e788cb7367d818d"}'
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'x86/{%position%}/chrome'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_TAR_ZSTD
  archive_data.tar_zstd_params.compression_level = 9
  archive_data.revisions_file.gcs_path = 'x86/{%position%}/REVISIONS'
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_tar_zstd_with_compression_level',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
        'got_v8_revision': '466dd2d77f6dd56a9174d7389e788cb7367d818d',
        'got_v8_revision_cp': 'refs/heads/9.7.48@{#1}',
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'x86/{%position%}/chrome'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_SQUASHFS
  archive_data.squashfs_params.algorithm = 'zstd'
  archive_data.squashfs_params.compression_level = 9
  archive_data.squashfs_params.block_size = '256K'
  archive_data.revisions_file.gcs_path = 'x86/{%position%}/REVISIONS'
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_squashfs_with_compression_algorithm',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
        'got_v8_revision': '466dd2d77f6dd56a9174d7389e788cb7367d818d',
        'got_v8_revision_cp': 'refs/heads/9.7.48@{#1}',
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'x86/{%position%}/chrome'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_SQUASHFS
  archive_data.squashfs_algorithm = 'zstd'
  archive_data.revisions_file.gcs_path = 'x86/{%position%}/REVISIONS'
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_squashfs_with_compression_algorithm_deprecated',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
        'got_v8_revision': '466dd2d77f6dd56a9174d7389e788cb7367d818d',
        'got_v8_revision_cp': 'refs/heads/9.7.48@{#1}',
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'x86/{%chrome_version%}/chrome'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_with_custom_vars',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      custom_vars={
        'chrome_version': '1.2.3.4',
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'x86/{%wrong_placeholder%}/chrome'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_with_wrong_custom_vars',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      custom_vars={
        'chrome_version': '1.2.3.4',
      },
      **{'$build/archive': input_properties},
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = '{%gcs_bucket%}'
  archive_data.gcs_path = 'x86/{%position%}/chrome'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_with_custom_var_gcs_bucket',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      custom_vars={
        'gcs_bucket': 'foo',
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.gsutil upload x86/123456/chrome',
      ['gs://foo/x86/123456/chrome'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'x86/{%position%}/chrome'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  archive_data.latest_upload.gcs_path = "x86/latest/latest.txt"
  archive_data.latest_upload.gcs_file_content = '{%chromium_version%}'
  archive_data.latest_upload.gcs_bucket = '{%latest_gcs_bucket%}'
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_with_custom_var_latest_gcs_bucket',
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      custom_vars={
        'latest_gcs_bucket': 'foo-latest',
      },
      **{'$build/archive': input_properties},
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.gsutil upload foo-latest/x86/latest/latest.txt',
      ['gs://foo-latest/x86/latest/latest.txt'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'dest_dir/'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_RECURSIVE
  input_properties.archive_datas.extend([archive_data])
  yield api.test(
    'Raw dir archive',
    api.properties(
      gcs_archive=True,
      update_properties={},
      **{'$build/archive': input_properties},
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.gsutil upload dest_dir/',
      ['-R'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'dest_dir/'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_RECURSIVE
  input_properties.archive_datas.extend([archive_data])
  yield api.test(
    'No dirs for ARCHIVE_TYPE_RECURSIVE',
    api.properties(
      gcs_archive=True,
      update_properties={},
      **{'$build/archive': input_properties},
    ),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  for archive_type in [
    properties.ArchiveData.ARCHIVE_TYPE_FILES,
    properties.ArchiveData.ARCHIVE_TYPE_FLATTEN_FILES,
  ]:
    input_properties = properties.InputProperties()
    archive_data = properties.ArchiveData()
    archive_data.dirs.extend(['anydir'])
    archive_data.gcs_bucket = 'any-bucket'
    archive_data.gcs_path = 'dest_dir/'
    archive_data.archive_type = archive_type
    input_properties.archive_datas.extend([archive_data])
    yield api.test(
      'generic_archive_dirs unsupported for %s' % archive_type,
      api.properties(
        gcs_archive=True,
        update_properties={},
        **{'$build/archive': input_properties},
      ),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
    )

  input_properties = properties.InputProperties(
    archive_datas=[
      {
        'files': ['path/to/some/file.txt'],
        'gcs_bucket': 'any-bucket',
        'gcs_path': 'dest_dir/',
        'archive_type': properties.ArchiveData.ARCHIVE_TYPE_FILES,
        'requires_provenance': True,
        'base_dir': 'src-internal',
      },
      {
        'files': ['path/to/file.txt', 'path/tp/file2.txt'],
        'gcs_bucket': 'any-bucket',
        'gcs_path': 'dest_dir/files.zip',
        'archive_type': properties.ArchiveData.ARCHIVE_TYPE_ZIP,
        'requires_provenance': True,
        'base_dir': 'src-internal',
      },
    ],
  )

  yield api.test(
    'experimental',
    api.runtime(is_experimental=True),
    api.properties(
      gcs_archive=True,
      update_properties={},
      **{'$build/archive': input_properties},
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'x86//chrome'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  archive_data.latest_upload.gcs_path = "x86/latest/latest.txt"
  archive_data.latest_upload.gcs_file_content = '{%chromium_version%}'
  archive_data.latest_upload.gcs_bucket = 'latest-bucket'
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'deconstruct_version',
    api.chromium.ci_build(builder='test'),
    api.properties(
      gcs_archive=True,
      update_properties={},
      **{'$build/archive': input_properties},
    ),
    api.step_data(
      'Generic Archiving Steps.get version',
      api.file.read_text('MAJOR=0\nMINOR=0\nBUILD=0\nPATCH=0'),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Write latest file',
      ['1.2.3.4'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'if_latest_gcs_bucket_set',
    api.chromium.ci_build(builder='test'),
    api.properties(
      gcs_archive=True,
      update_properties={},
      **{'$build/archive': input_properties},
    ),
    api.step_data(
      'Generic Archiving Steps.get version',
      api.file.read_text('MAJOR=0\nMINOR=0\nBUILD=0\nPATCH=0'),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Write latest file',
      ['1.2.3.4'],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.gsutil upload '
      'latest-bucket/x86/latest/latest.txt',
      ['gs://latest-bucket/x86/latest/latest.txt'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'if_last_version_is_older',
    api.chromium.ci_build(builder='test'),
    api.properties(
      gcs_archive=True,
      update_properties={},
      **{'$build/archive': input_properties},
    ),
    api.step_data(
      'Generic Archiving Steps.get version',
      api.file.read_text('MAJOR=90\nMINOR=1\nBUILD=2\nPATCH=3'),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Write latest file',
      ['90.1.2.3'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'if_latest_path_does_not_exist',
    api.chromium.ci_build(builder='test'),
    api.properties(
      gcs_archive=True,
      update_properties={},
      **{'$build/archive': input_properties},
    ),
    api.step_data('Generic Archiving Steps.gsutil download', retcode=1),
    api.step_data(
      'Generic Archiving Steps.get version',
      api.file.read_text('MAJOR=90\nMINOR=1\nBUILD=2\nPATCH=3'),
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Write latest file',
      ['90.1.2.3'],
    ),
    api.post_process(post_process.StepSuccess, 'Generic Archiving Steps'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'test_get_channel_name',
    api.properties(test_get_channel_name=True, channel='canary'),
    api.step_data('get version', api.file.read_text(TEST_CHROME_VERSION)),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties(
    archive_datas=[
      {
        'files': ['path/to/some/file.txt'],
        'gcs_bucket': 'any-bucket',
        'gcs_path': '{%milestone_position%}/',
        'archive_type': properties.ArchiveData.ARCHIVE_TYPE_FILES,
      },
    ],
  )

  yield api.test(
    'test_get_milestone_position_basic',
    api.properties(
      gcs_archive=True,
      update_properties={},
      **{'$build/archive': input_properties},
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'test_get_previous_milestone_position',
    api.properties(
      test_get_milestone_position=True, milestone_position='canary-1'
    ),
    api.step_data(
      'get version',
      api.file.read_text('MAJOR=90\nMINOR=0\nBUILD=4711\nPATCH=0'),
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'foo/bar'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  archive_data.prevent_overwrites = True
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_with_prevent_overwrites',
    api.properties(gcs_archive=True, **{'$build/archive': input_properties}),
    api.post_process(
      post_process.StepCommandContains,
      "Generic Archiving Steps.gsutil upload foo/bar",
      [
        "cp",
        "-n",
      ],
    ),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'x86/{%position%}_{%commit%}_{%timestamp%}/chrome.zip'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  archive_data.latest_upload.gcs_path = "x86/latest/latest.txt"
  archive_data.latest_upload.gcs_file_content = (
    '{%position%}_{%commit%}_{%timestamp%}'
  )
  archive_data.requires_provenance = True
  archive_data.requires_sbom.gn_targets.append("//my-target")
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_with_generate_sbom',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      **{'$build/archive': input_properties},
    ),
    api.override_step_data(
      "Generic Archiving Steps.SSCI collection.run depbot",
      api.json.output(
        name="summary",
        data={
          "targets": [
            {
              "entry_point": "//my-target",
              "target": "//my-target",
              "artifacts_file_path": "out/Release/artifacts.json",
              "libraries_file_path": "out/Release/libs.json",
            }
          ],
          "archive": {
            "name": "generic_archive",
            "targets": ["//my-target"],
            "artifacts_file_path": "out/Release/artifacts.json",
            "libraries_file_path": "out/Release/libs.json",
          },
        },
      ),
    ),
    api.post_process(
      post_process.MustRun, 'Generic Archiving Steps.SSCI collection'
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Compute file hash',
      [
        "vpython3",
        "-u",
        "RECIPE_MODULE[recipe_engine::file]/resources/fileutil.py",
        "--json-output",
        "/path/to/tmp/json",
        "file_hash",
        "[CLEANUP]/tmp_tmp_3/chrome.zip.spdx.json",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Compute file hash (2)',
      [
        "vpython3",
        "-u",
        "RECIPE_MODULE[recipe_engine::file]/resources/fileutil.py",
        "--json-output",
        "/path/to/tmp/json",
        "file_hash",
        "[CLEANUP]/tmp_tmp_3/artifact.zip",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.snoop: report_sbom',
      [
        "-report-gcs",
        "-digest",
        "spdxbeef",
        "-gcs-uri",
        "gs://any-bucket/x86/123456_5e3250aadda2b170692f8e762d43b7e8deadbeef_20120514125321/chrome.zip.spdx.json",
        "-sbom-subject",
        "deadbeef",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.gsutil upload x86/123456_5e3250aadda2b170692f8e762d43b7e8deadbeef_20120514125321/chrome.zip.spdx.json',
      [
        "python3",
        "-u",
        "RECIPE_MODULE[depot_tools::gsutil]/resources/gsutil_smart_retry.py",
        "--",
        "RECIPE_REPO[depot_tools]/gsutil.py",
        "----",
        "cp",
        "[CLEANUP]/tmp_tmp_3/chrome.zip.spdx.json",
        "gs://any-bucket/x86/123456_5e3250aadda2b170692f8e762d43b7e8deadbeef_20120514125321/chrome.zip.spdx.json",
      ],
    ),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.files.extend(['path/to/file.txt', 'path/to/file2.txt'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'somedir/{%builder_name%}/'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_FLATTEN_FILES
  archive_data.latest_upload.gcs_path = "x86/latest/latest.txt"
  archive_data.latest_upload.gcs_file_content = (
    '{%position%}_{%commit%}_{%timestamp%}'
  )
  archive_data.requires_provenance = True
  archive_data.requires_sbom.gn_targets.append("//my-target")
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_flatten_files_type_with_generate_sbom',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      **{'$build/archive': input_properties},
    ),
    api.override_step_data(
      "Generic Archiving Steps.SSCI collection.run depbot",
      api.json.output(
        name="summary",
        data={
          "targets": [
            {
              "entry_point": "//my-target",
              "target": "//my-target",
              "artifacts_file_path": "out/Release/artifacts.json",
              "libraries_file_path": "out/Release/libs.json",
            }
          ],
          "archive": {
            "name": "generic_archive",
            "targets": ["//my-target"],
            "artifacts_file_path": "out/Release/artifacts.json",
            "libraries_file_path": "out/Release/libs.json",
          },
        },
      ),
    ),
    api.post_process(
      post_process.MustRun, 'Generic Archiving Steps.SSCI collection'
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Compute file hash',
      [
        "vpython3",
        "-u",
        "RECIPE_MODULE[recipe_engine::file]/resources/fileutil.py",
        "--json-output",
        "/path/to/tmp/json",
        "file_hash",
        "[CLEANUP]/tmp_tmp_2/path/to/file.txt.spdx.json",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Compute file hash (2)',
      [
        "vpython3",
        "-u",
        "RECIPE_MODULE[recipe_engine::file]/resources/fileutil.py",
        "--json-output",
        "/path/to/tmp/json",
        "file_hash",
        "[CLEANUP]/tmp_tmp_2/path/to/file.txt",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Compute file hash (4)',
      [
        "vpython3",
        "-u",
        "RECIPE_MODULE[recipe_engine::file]/resources/fileutil.py",
        "--json-output",
        "/path/to/tmp/json",
        "file_hash",
        "[CLEANUP]/tmp_tmp_2/path/to/file2.txt",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.snoop: report_sbom',
      [
        "-report-gcs",
        "-digest",
        "spdxbeef",
        "-gcs-uri",
        "gs://any-bucket/somedir/test_buildername/file.txt.spdx.json",
        "-sbom-subject",
        "deadbeef",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.gsutil upload somedir/test_buildername/file.txt.spdx.json',
      [
        "python3",
        "-u",
        "RECIPE_MODULE[depot_tools::gsutil]/resources/gsutil_smart_retry.py",
        "--",
        "RECIPE_REPO[depot_tools]/gsutil.py",
        "----",
        "cp",
        "[CLEANUP]/tmp_tmp_2/path/to/file.txt.spdx.json",
        "gs://any-bucket/somedir/test_buildername/file.txt.spdx.json",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Compute file hash (3)',
      [
        "vpython3",
        "-u",
        "RECIPE_MODULE[recipe_engine::file]/resources/fileutil.py",
        "--json-output",
        "/path/to/tmp/json",
        "file_hash",
        "[CLEANUP]/tmp_tmp_2/path/to/file2.txt.spdx.json",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.snoop: report_sbom (2)',
      [
        "-report-gcs",
        "-digest",
        "spdxbeef",
        "-gcs-uri",
        "gs://any-bucket/somedir/test_buildername/file2.txt.spdx.json",
        "-sbom-subject",
        "deadbeef",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.gsutil upload somedir/test_buildername/file2.txt.spdx.json',
      [
        "python3",
        "-u",
        "RECIPE_MODULE[depot_tools::gsutil]/resources/gsutil_smart_retry.py",
        "--",
        "RECIPE_REPO[depot_tools]/gsutil.py",
        "----",
        "cp",
        "[CLEANUP]/tmp_tmp_2/path/to/file2.txt.spdx.json",
        "gs://any-bucket/somedir/test_buildername/file2.txt.spdx.json",
      ],
    ),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.files.extend(['file.txt', 'file2.txt'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'somedir/{%builder_name%}'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_FILES
  archive_data.latest_upload.gcs_path = "x86/latest/latest.txt"
  archive_data.latest_upload.gcs_file_content = (
    '{%position%}_{%commit%}_{%timestamp%}'
  )
  archive_data.requires_provenance = True
  archive_data.requires_sbom.gn_targets.append("//my-target")
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_files_type_with_generate_sbom',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      **{'$build/archive': input_properties},
    ),
    api.override_step_data(
      "Generic Archiving Steps.SSCI collection.run depbot",
      api.json.output(
        name="summary",
        data={
          "targets": [
            {
              "entry_point": "//my-target",
              "target": "//my-target",
              "artifacts_file_path": "out/Release/artifacts.json",
              "libraries_file_path": "out/Release/libs.json",
            }
          ],
          "archive": {
            "name": "generic_archive",
            "targets": ["//my-target"],
            "artifacts_file_path": "out/Release/artifacts.json",
            "libraries_file_path": "out/Release/libs.json",
          },
        },
      ),
    ),
    api.post_process(
      post_process.MustRun, 'Generic Archiving Steps.SSCI collection'
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Compute file hash',
      [
        "vpython3",
        "-u",
        "RECIPE_MODULE[recipe_engine::file]/resources/fileutil.py",
        "--json-output",
        "/path/to/tmp/json",
        "file_hash",
        "[CLEANUP]/tmp_tmp_2/file.txt.spdx.json",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Compute file hash (2)',
      [
        "vpython3",
        "-u",
        "RECIPE_MODULE[recipe_engine::file]/resources/fileutil.py",
        "--json-output",
        "/path/to/tmp/json",
        "file_hash",
        "[CLEANUP]/tmp_tmp_2/file.txt",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Compute file hash (4)',
      [
        "vpython3",
        "-u",
        "RECIPE_MODULE[recipe_engine::file]/resources/fileutil.py",
        "--json-output",
        "/path/to/tmp/json",
        "file_hash",
        "[CLEANUP]/tmp_tmp_2/file2.txt",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.snoop: report_sbom',
      [
        "-report-gcs",
        "-digest",
        "spdxbeef",
        "-gcs-uri",
        "gs://any-bucket/somedir/test_buildername/file.txt.spdx.json",
        "-sbom-subject",
        "deadbeef",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.gsutil upload somedir/test_buildername/file.txt.spdx.json',
      [
        "python3",
        "-u",
        "RECIPE_MODULE[depot_tools::gsutil]/resources/gsutil_smart_retry.py",
        "--",
        "RECIPE_REPO[depot_tools]/gsutil.py",
        "----",
        "cp",
        "[CLEANUP]/tmp_tmp_2/file.txt.spdx.json",
        "gs://any-bucket/somedir/test_buildername/file.txt.spdx.json",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.Compute file hash (3)',
      [
        "vpython3",
        "-u",
        "RECIPE_MODULE[recipe_engine::file]/resources/fileutil.py",
        "--json-output",
        "/path/to/tmp/json",
        "file_hash",
        "[CLEANUP]/tmp_tmp_2/file2.txt.spdx.json",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.snoop: report_sbom (2)',
      [
        "-report-gcs",
        "-digest",
        "spdxbeef",
        "-gcs-uri",
        "gs://any-bucket/somedir/test_buildername/file2.txt.spdx.json",
        "-sbom-subject",
        "deadbeef",
      ],
    ),
    api.post_process(
      post_process.StepCommandContains,
      'Generic Archiving Steps.gsutil upload somedir/test_buildername/file2.txt.spdx.json',
      [
        "python3",
        "-u",
        "RECIPE_MODULE[depot_tools::gsutil]/resources/gsutil_smart_retry.py",
        "--",
        "RECIPE_REPO[depot_tools]/gsutil.py",
        "----",
        "cp",
        "[CLEANUP]/tmp_tmp_2/file2.txt.spdx.json",
        "gs://any-bucket/somedir/test_buildername/file2.txt.spdx.json",
      ],
    ),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  input_properties = properties.InputProperties()
  archive_data = properties.ArchiveData()
  archive_data.dirs.extend(['anydir'])
  archive_data.gcs_bucket = 'any-bucket'
  archive_data.gcs_path = 'x86/{%position%}_{%commit%}_{%timestamp%}/chrome.zip'
  archive_data.archive_type = properties.ArchiveData.ARCHIVE_TYPE_ZIP
  archive_data.latest_upload.gcs_path = "x86/latest/latest.txt"
  archive_data.latest_upload.gcs_file_content = (
    '{%position%}_{%commit%}_{%timestamp%}'
  )
  archive_data.requires_provenance = True
  archive_data.requires_sbom.gn_targets.append("//my-target")
  input_properties.archive_datas.extend([archive_data])

  yield api.test(
    'generic_archive_with_generate_sbom_failure',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      **{'$build/archive': input_properties},
    ),
    api.override_step_data(
      'Generic Archiving Steps.move and rename SBOM to match artifact',
      retcode=1,
    ),
    api.override_step_data(
      "Generic Archiving Steps.SSCI collection.run depbot",
      api.json.output(
        name="summary",
        data={
          "targets": [
            {
              "entry_point": "//my-target",
              "target": "//my-target",
              "artifacts_file_path": "out/Release/artifacts.json",
              "libraries_file_path": "out/Release/libs.json",
            }
          ],
          "archive": {
            "name": "generic_archive",
            "targets": ["//my-target"],
            "artifacts_file_path": "out/Release/artifacts.json",
            "libraries_file_path": "out/Release/libs.json",
          },
        },
      ),
    ),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'generic_archive_with_report_sbom_failure',
    api.chromium.ci_build(
      builder_group='test_group',
      builder='test_buildername',
    ),
    api.properties(
      gcs_archive=True,
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      **{'$build/archive': input_properties},
    ),
    api.override_step_data(
      'Generic Archiving Steps.snoop: report_sbom', retcode=1
    ),
    api.override_step_data(
      'Generic Archiving Steps.snoop: report_sbom (2)', retcode=1
    ),
    api.override_step_data(
      'Generic Archiving Steps.snoop: report_sbom (3)', retcode=1
    ),
    api.override_step_data(
      'Generic Archiving Steps.snoop: report_sbom (4)', retcode=1
    ),
    api.override_step_data(
      "Generic Archiving Steps.SSCI collection.run depbot",
      api.json.output(
        name="summary",
        data={
          "targets": [
            {
              "entry_point": "//my-target",
              "target": "//my-target",
              "artifacts_file_path": "out/Release/artifacts.json",
              "libraries_file_path": "out/Release/libs.json",
            }
          ],
          "archive": {
            "name": "generic_archive",
            "targets": ["//my-target"],
            "artifacts_file_path": "out/Release/artifacts.json",
            "libraries_file_path": "out/Release/libs.json",
          },
        },
      ),
    ),
    api.expect_status('SUCCESS'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'cf_archiving_schema_v1',
    api.platform('linux', 64),
    api.properties(
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      archive_schema_version=1,
      compile_targets=['chrome'],
    ),
    api.post_process(
      post_process.MustRun, 'collect runtime deps for compile targets'
    ),
    api.post_process(
      post_process.MustRun,
      'collect runtime deps for compile targets.read chrome.runtime_deps',
    ),
    api.post_process(post_process.MustRun, 'write archive manifest'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'cf_archiving_schema_v1_missing_compile_targets',
    api.platform('linux', 64),
    api.properties(
      update_properties={
        'got_revision': TEST_HASH_MAIN,
        'got_revision_cp': TEST_COMMIT_POSITON_MAIN,
      },
      archive_schema_version=1,
    ),
    api.expect_exception('ValueError'),
    api.post_process(post_process.DropExpectation),
  )
