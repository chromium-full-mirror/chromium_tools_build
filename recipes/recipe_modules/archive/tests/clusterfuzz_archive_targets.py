# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import datetime
import json

from recipe_engine.post_process import (
  DoesNotRun,
  DropExpectation,
  LogEquals,
  MustRun,
  StepCommandContains,
)

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import archive
from RECIPE_MODULES.depot_tools import tryserver
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  file,
  path,
  platform,
  properties,
  runtime,
)


@dataclass
class DEPS(RecipeScriptApi):
  archive: archive.API
  buildbucket: buildbucket.API
  path: path.API
  properties: properties.API
  runtime: runtime.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  file: file.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  runtime: runtime.TEST_API


def RunSteps(api: DEPS):
  source_dir = api.path.cache_dir / 'builder/src'
  build_dir = source_dir / 'out/Release'

  archive_root_type = api.properties.get('archive_root_type', 'source')
  archive_root = build_dir if archive_root_type == 'build' else source_dir

  update_properties = {
    'got_revision': 'abcdefgh' * 5,
    'got_revision_cp': 'refs/heads/main@{#123456}',
  }

  kwargs = {**api.properties.get('clusterfuzz_archive_kwargs', {})}
  kwargs.setdefault('archive_path', 'linux-release/chrome-asan-linux-release')
  kwargs.setdefault('gs_bucket', 'chromium')
  kwargs.setdefault('archive_schema_version', 1)
  kwargs.setdefault('compile_targets', ['target1'])

  api.archive.clusterfuzz_archive_targets(
    source_dir=source_dir,
    archive_root=archive_root,
    update_properties=update_properties,
    build_dir=build_dir,
    **kwargs,
  )


def GenTests(api: TEST_DEPS):
  expected_zip_paths = json.dumps(
    [
      'clusterfuzz_manifest.json',
      'out/Release/args.gn',
      'out/Release/target1',
      'out/Release/target1.runtime_deps',
      'out/Release/target1_dependency',
      'testing/data/fuzzer_seed.txt',
    ]
  )

  yield api.test(
    'basic',
    # Tests that everything goes correctly.
    api.properties(
      clusterfuzz_archive_kwargs={
        'fuzz_targets': ['target1'],
      },
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
        sort_keys=True,
      ),
    ),
    api.post_process(
      StepCommandContains,
      'zipping',
      [
        '--json-file-list',
        expected_zip_paths,
      ],
    ),
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
      json.dumps(
        {
          'archive_schema_version': 1,
        },
        sort_keys=True,
      ),
    ),
    api.post_process(
      StepCommandContains,
      'zipping',
      [
        '--json-file-list',
        expected_zip_paths,
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'empty_compile_targets',
    api.properties(
      clusterfuzz_archive_kwargs={'compile_targets': []},
    ),
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
      json.dumps(
        {
          'archive_schema_version': 0,
          'fuzz_targets': ['target1'],
        },
        sort_keys=True,
      ),
    ),
    api.post_process(
      StepCommandContains,
      'zipping',
      [
        '--json-file-list',
        json.dumps(
          [
            'clusterfuzz_manifest.json',
            'args.gn',
            '../../testing/data/fuzzer_seed.txt',
            'target1',
            'target1.runtime_deps',
            'target1_dependency',
          ]
        ),
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'verbatim',
    api.properties(
      clusterfuzz_archive_kwargs={
        'fuzz_targets': ['target1'],
        'gs_bucket': 'bleep-bloop',
        'archive_path': 'foo-x64/asan-bar',
      },
    ),
    api.post_process(
      StepCommandContains,
      'zipping',
      [
        '--archive-name',
        'asan-bar-123456',
      ],
    ),
    api.post_process(MustRun, 'gsutil upload'),
    api.post_process(
      StepCommandContains,
      'gsutil upload',
      [
        'gs://bleep-bloop/foo-x64/asan-bar-123456.zip',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'verbatim_sortkey_datetime',
    api.properties(
      clusterfuzz_archive_kwargs={
        'fuzz_targets': ['target1'],
        'gs_bucket': 'bleep-bloop',
        'archive_path': 'foo-x64/asan-bar',
        'sortkey_datetime': datetime.datetime(2026, 6, 22, 15, 53),
      },
    ),
    api.post_process(
      StepCommandContains,
      'zipping',
      [
        '--archive-name',
        'asan-bar-202606221553',
      ],
    ),
    api.post_process(MustRun, 'gsutil upload'),
    api.post_process(
      StepCommandContains,
      'gsutil upload',
      [
        'gs://bleep-bloop/foo-x64/asan-bar-202606221553.zip',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'verbatim experimental',
    api.properties(
      clusterfuzz_archive_kwargs={
        'fuzz_targets': ['target1'],
        'gs_bucket': 'bleep-bloop',
        'archive_path': 'foo-x64/asan-bar',
      },
    ),
    api.runtime(is_experimental=True),
    api.post_process(
      StepCommandContains,
      'zipping',
      [
        '--archive-name',
        'asan-bar-123456-experimental',
      ],
    ),
    api.post_process(MustRun, 'gsutil upload'),
    api.post_process(
      StepCommandContains,
      'gsutil upload',
      [
        'gs://bleep-bloop/foo-x64/asan-bar-123456-experimental.zip',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'assert_success',
    api.properties(
      clusterfuzz_archive_kwargs={
        'fuzz_targets': ['target1'],
        'archive_path': 'linux-release/chrome-asan-linux-release',
      },
    ),
    api.post_process(MustRun, 'gsutil upload'),
    api.post_process(
      StepCommandContains,
      'gsutil upload',
      [
        'gs://chromium/linux-release/chrome-asan-linux-release-123456.zip',
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'assert_failure',
    api.properties(
      clusterfuzz_archive_kwargs={
        'fuzz_targets': ['target1'],
        'archive_path': '',
      },
    ),
    api.expect_exception('AssertionError'),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'tryserver',
    api.properties(
      clusterfuzz_archive_kwargs={
        'compile_targets': ['target1', 'target2'],
        'fuzz_targets': ['target1'],
      },
    ),
    api.buildbucket.try_build(),
    api.post_process(MustRun, 'collect runtime deps for compile targets'),
    api.post_process(
      MustRun,
      'collect runtime deps for compile targets.read target1.runtime_deps',
    ),
    api.post_process(
      MustRun,
      'collect runtime deps for compile targets.read target2.runtime_deps',
    ),
    api.post_process(
      StepCommandContains,
      'zipping',
      [
        '--json-file-list',
        json.dumps(['clusterfuzz_manifest.json', 'out/Release/args.gn']),
      ],
    ),
    api.post_process(DropExpectation),
  )

  yield api.test(
    'exe_target',
    api.properties(
      clusterfuzz_archive_kwargs={
        'compile_targets': ['target1.exe'],
        'fuzz_targets': ['target1.exe'],
      },
    ),
    api.post_process(MustRun, 'collect runtime deps for compile targets'),
    api.post_process(
      MustRun,
      'collect runtime deps for compile targets.read target1.runtime_deps',
    ),
    api.post_process(DropExpectation),
  )

  def runtime_deps_test(
    *, name, target_to_deps, expected_runtime_deps, is_win=False
  ):
    """Builds a test case asserting the runtime deps that get zipped.

    Args:
      name: Name of the test case.
      target_to_deps: Mapping of compile target name to the contents of its
        `.runtime_deps` file.
      expected_runtime_deps: Expected runtime deps passed to the zipping step,
        in order, after the manifest and `args.gn`.
      is_win: Whether to run on Windows, where paths use `\\` separators.
    """
    args_gn = 'out\\Release\\args.gn' if is_win else 'out/Release/args.gn'
    deps_data = [
      api.step_data(
        f'collect runtime deps for compile targets.read {target}.runtime_deps',
        api.file.read_text(deps_text),
      )
      for target, deps_text in target_to_deps.items()
    ]
    return api.test(
      name,
      api.properties(
        clusterfuzz_archive_kwargs={
          'compile_targets': list(target_to_deps),
          'fuzz_targets': ['target1'],
        },
      ),
      *deps_data,
      *([api.platform('win', 64)] if is_win else []),
      api.post_process(
        StepCommandContains,
        'zipping',
        [
          '--json-file-list',
          json.dumps(
            ['clusterfuzz_manifest.json', args_gn, *expected_runtime_deps]
          ),
        ],
      ),
      api.post_process(DropExpectation),
    )

  # A directory plus paths nested inside it. The nested paths are redundant;
  # sibling `data-foo/x.json` only shares a string prefix and must be kept.
  overlapping_deps = (
    './target1\n'
    '../../chrome/test/data/\n'
    '../../chrome/test/data/cart\n'
    '../../chrome/test/data/cart/item.json\n'
    '../../chrome/test/data-foo/x.json\n'
  )

  yield runtime_deps_test(
    name='overlapping_runtime_deps',
    target_to_deps={'target1': overlapping_deps},
    expected_runtime_deps=[
      'chrome/test/data',
      'chrome/test/data-foo/x.json',
      'out/Release/target1',
      'out/Release/target1.runtime_deps',
    ],
  )

  # Same overlap on Windows: paths render with the platform separator.
  yield runtime_deps_test(
    name='overlapping_runtime_deps_win',
    target_to_deps={'target1': overlapping_deps},
    expected_runtime_deps=[
      'chrome\\test\\data',
      'chrome\\test\\data-foo\\x.json',
      'out\\Release\\target1',
      'out\\Release\\target1.runtime_deps',
    ],
    is_win=True,
  )

  # Entries must be normalized before the ancestor check, or `foo/../chrome/...`
  # would look like a descendant of the `foo` entry and be dropped.
  traversal_deps = (
    './target1\n'
    '../../foo\n'
    '../../foo/../chrome/test/data\n'
    '../../chrome/test/data/cart/../cart/item.json\n'
  )

  yield runtime_deps_test(
    name='runtime_deps_with_path_traversal',
    target_to_deps={'target1': traversal_deps},
    # `chrome/test/data` escapes `foo` and is kept; `data/cart/item.json` is a
    # true descendant and is dropped.
    expected_runtime_deps=[
      'chrome/test/data',
      'foo',
      'out/Release/target1',
      'out/Release/target1.runtime_deps',
    ],
  )

  # Same traversal with backslashes, which are only split apart on Windows.
  yield runtime_deps_test(
    name='runtime_deps_with_path_traversal_win',
    target_to_deps={
      'target1': (
        '.\\target1\n'
        '..\\..\\foo\n'
        '..\\..\\foo\\..\\chrome\\test\\data\n'
        '..\\..\\chrome\\test\\data\\cart\\..\\cart\\item.json\n'
      ),
    },
    expected_runtime_deps=[
      'chrome\\test\\data',
      'foo',
      'out\\Release\\target1',
      'out\\Release\\target1.runtime_deps',
    ],
    is_win=True,
  )

  # Overlapping deps are also deduped across targets, since a descendant
  # declared by one target is still covered by another target's directory.
  yield runtime_deps_test(
    name='overlapping_runtime_deps_across_targets',
    target_to_deps={
      'target1': './target1\n../../chrome/test/data/cart/item.json\n',
      'target2': './target2\n../../chrome/test/data\n',
    },
    expected_runtime_deps=[
      'chrome/test/data',
      'out/Release/target1',
      'out/Release/target1.runtime_deps',
      'out/Release/target2',
      'out/Release/target2.runtime_deps',
    ],
  )
