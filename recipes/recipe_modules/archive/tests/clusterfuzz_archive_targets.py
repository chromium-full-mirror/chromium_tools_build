# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import json

from recipe_engine.post_process import (DoesNotRun, DropExpectation, LogEquals,
                                        MustRun, StepCommandContains)

DEPS = [
    'archive',
    'recipe_engine/path',
    'recipe_engine/properties',
]


def RunSteps(api):
  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = source_dir / 'out/Release'

  archive_root_type = api.properties.get('archive_root_type', 'source')
  archive_root = build_dir if archive_root_type == 'build' else source_dir

  archive_schema_version = api.properties.get('archive_schema_version', 1)
  compile_targets = api.properties.get('compile_targets', ['target1'])
  fuzz_targets = api.properties.get('fuzz_targets')
  update_properties = api.properties.get('update_properties')

  api.archive.clusterfuzz_archive_targets(
      source_dir=source_dir,
      archive_root=archive_root,
      update_properties=update_properties,
      gs_bucket='chromium',
      archive_prefix='chrome-asan',
      build_config='Release',
      compile_targets=compile_targets,
      build_dir=build_dir,
      archive_schema_version=archive_schema_version,
      fuzz_targets=fuzz_targets,
  )


def GenTests(api):
  update_properties = {
      'got_revision': 'abcdefgh' * 5,
      'got_revision_cp': 'refs/heads/main@{#123456}',
  }

  expected_zip_paths = json.dumps([
      'out/Release/target1',
      'out/Release/target1.runtime_deps',
      'out/Release/target1_dependency',
      'testing/data/fuzzer_seed.txt',
      'clusterfuzz_manifest.json',
  ])

  yield api.test(
      'basic',
      # Tests that everything goes correctly.
      api.properties(
          update_properties=update_properties,
          fuzz_targets=['target1'],
      ),
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
      api.properties(update_properties=update_properties),
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
      api.properties(
          update_properties=update_properties,
          compile_targets=[],
      ),
      api.expect_exception('ValueError'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'schema_v0',
      # Tests that when archive_root is build_dir (legacy schema v0)
      api.properties(
          update_properties=update_properties,
          fuzz_targets=['target1'],
          archive_schema_version=0,
          archive_root_type='build',
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
      api.post_process(DropExpectation),
  )
