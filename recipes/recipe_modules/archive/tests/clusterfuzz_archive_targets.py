# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import datetime
import json

from recipe_engine.post_process import (DoesNotRun, DropExpectation, LogEquals,
                                        MustRun, StepCommandContains)

DEPS = [
    'archive',
    'depot_tools/tryserver',
    'recipe_engine/buildbucket',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/runtime',
]


def RunSteps(api):
  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = source_dir / 'out/Release'

  archive_root_type = api.properties.get('archive_root_type', 'source')
  archive_root = build_dir if archive_root_type == 'build' else source_dir

  update_properties = {
      'got_revision': 'abcdefgh' * 5,
      'got_revision_cp': 'refs/heads/main@{#123456}',
  }

  kwargs = {**api.properties.get('clusterfuzz_archive_kwargs', {})}
  kwargs.setdefault('archive_prefix', 'chrome-asan')
  kwargs.setdefault('gs_bucket', 'chromium')
  kwargs.setdefault('archive_schema_version', 1)
  kwargs.setdefault('compile_targets', ['target1'])

  api.archive.clusterfuzz_archive_targets(
      source_dir=source_dir,
      archive_root=archive_root,
      update_properties=update_properties,
      build_config='Release',
      build_dir=build_dir,
      **kwargs)


def GenTests(api):
  expected_zip_paths = json.dumps([
      'clusterfuzz_manifest.json',
      'out/Release/args.gn',
      'out/Release/target1',
      'out/Release/target1.runtime_deps',
      'out/Release/target1_dependency',
      'testing/data/fuzzer_seed.txt',
  ])

  yield api.test(
      'basic',
      # Tests that everything goes correctly.
      api.properties(
          clusterfuzz_archive_kwargs={
              'fuzz_targets': ['target1'],
          },),
      api.post_process(MustRun, 'collect runtime deps for compile targets'),
      api.post_process(
          LogEquals,
          'write archive manifest',
          'clusterfuzz_manifest.json',
          json.dumps(
              {
                  'archive_schema_version': 1,
                  'fuzz_targets': ['out/Release/target1'],
              },
              sort_keys=True),
      ),
      api.post_process(StepCommandContains, 'zipping', [
          '--json-file-list',
          expected_zip_paths,
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'no_fuzz_targets',
      api.properties(),
      api.post_process(MustRun, 'collect runtime deps for compile targets'),
      api.post_process(
          LogEquals,
          'write archive manifest',
          'clusterfuzz_manifest.json',
          json.dumps({
              'archive_schema_version': 1,
          }, sort_keys=True),
      ),
      api.post_process(StepCommandContains, 'zipping', [
          '--json-file-list',
          expected_zip_paths,
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'empty_compile_targets',
      api.properties(clusterfuzz_archive_kwargs={'compile_targets': []},),
      api.expect_exception('ValueError'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'schema_v0',
      # Tests that when archive_root is build_dir (legacy schema v0)
      api.properties(
          archive_root_type='build',
          clusterfuzz_archive_kwargs={
              'fuzz_targets': ['target1'],
              'archive_schema_version': 0,
          },
      ),
      api.post_process(MustRun, 'collect runtime deps for compile targets'),
      api.post_process(
          LogEquals,
          'write archive manifest',
          'clusterfuzz_manifest.json',
          json.dumps({
              'archive_schema_version': 0,
              'fuzz_targets': ['target1'],
          },
                     sort_keys=True),
      ),
      api.post_process(StepCommandContains, 'zipping', [
          '--json-file-list',
          json.dumps([
              'clusterfuzz_manifest.json',
              'args.gn',
              '../../testing/data/fuzzer_seed.txt',
              'target1',
              'target1.runtime_deps',
              'target1_dependency',
          ]),
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'verbatim',
      # Tests handling of the `use_archive_path` argument.
      api.properties(
          clusterfuzz_archive_kwargs={
              'fuzz_targets': ['target1'],
              'gs_bucket': 'bleep-bloop',
              'archive_path': 'foo-x64/asan-bar',
              'use_archive_path': True,
          },),
      api.post_process(StepCommandContains, 'zipping', [
          '--archive-name',
          'asan-bar-123456',
      ]),
      api.post_process(MustRun, 'gsutil upload'),
      api.post_process(StepCommandContains, 'gsutil upload', [
          'gs://bleep-bloop/foo-x64/asan-bar-123456.zip',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'verbatim_sortkey_datetime',
      # Tests handling of the `use_archive_path` argument combined
      # with `sortkey_datetime`.
      api.properties(
          clusterfuzz_archive_kwargs={
              'fuzz_targets': ['target1'],
              'gs_bucket': 'bleep-bloop',
              'archive_path': 'foo-x64/asan-bar',
              'sortkey_datetime': datetime.datetime(2026, 6, 22, 15, 53),
              'use_archive_path': True,
          },),
      api.post_process(StepCommandContains, 'zipping', [
          '--archive-name',
          'asan-bar-202606221553',
      ]),
      api.post_process(MustRun, 'gsutil upload'),
      api.post_process(StepCommandContains, 'gsutil upload', [
          'gs://bleep-bloop/foo-x64/asan-bar-202606221553.zip',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'verbatim experimental',
      # Tests handling of the `use_archive_path` argument combined
      # with an experimental build.
      api.properties(
          clusterfuzz_archive_kwargs={
              'fuzz_targets': ['target1'],
              'gs_bucket': 'bleep-bloop',
              'archive_path': 'foo-x64/asan-bar',
              'use_archive_path': True,
          },),
      api.runtime(is_experimental=True),
      api.post_process(StepCommandContains, 'zipping', [
          '--archive-name',
          'asan-bar-123456-experimental',
      ]),
      api.post_process(MustRun, 'gsutil upload'),
      api.post_process(StepCommandContains, 'gsutil upload', [
          'gs://bleep-bloop/foo-x64/asan-bar-123456-experimental.zip',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'assert_success',
      # Tests assertion success when archive_path matches derived path.
      api.properties(
          clusterfuzz_archive_kwargs={
              'fuzz_targets': ['target1'],
              'archive_path': 'linux-release/chrome-asan-linux-release',
              'use_archive_path': False,
          },),
      api.post_process(MustRun, 'gsutil upload'),
      api.post_process(StepCommandContains, 'gsutil upload', [
          'gs://chromium/linux-release/chrome-asan-linux-release-123456.zip',
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'assert_failure',
      # Tests assertion failure when archive_path does not match derived path.
      api.properties(
          clusterfuzz_archive_kwargs={
              'fuzz_targets': ['target1'],
              'archive_path': 'wrong-path',
              'use_archive_path': False,
          },),
      api.expect_exception('AssertionError'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'tryserver',
      api.properties(
          clusterfuzz_archive_kwargs={
              'compile_targets': ['target1', 'target2'],
              'fuzz_targets': ['target1'],
          },),
      api.buildbucket.try_build(),
      api.post_process(MustRun, 'collect runtime deps for compile targets'),
      api.post_process(
          MustRun,
          'collect runtime deps for compile targets.read target1.runtime_deps'),
      api.post_process(
          MustRun,
          'collect runtime deps for compile targets.read target2.runtime_deps'),
      api.post_process(StepCommandContains, 'zipping', [
          '--json-file-list',
          json.dumps(['clusterfuzz_manifest.json', 'out/Release/args.gn']),
      ]),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'exe_target',
      api.properties(
          clusterfuzz_archive_kwargs={
              'compile_targets': ['target1.exe'],
              'fuzz_targets': ['target1.exe'],
          },),
      api.post_process(MustRun, 'collect runtime deps for compile targets'),
      api.post_process(
          MustRun,
          'collect runtime deps for compile targets.read target1.runtime_deps'),
      api.post_process(DropExpectation),
  )
