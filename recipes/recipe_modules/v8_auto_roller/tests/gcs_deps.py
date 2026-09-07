# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import DoesNotRun, DropExpectation, MustRun, StepCommandContains

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8_auto_roller
from RECIPE_MODULES.recipe_engine import file


@dataclass
class DEPS(RecipeScriptApi):
  file: file.API
  v8_auto_roller: v8_auto_roller.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  file: file.TEST_API

DEFAULT_V8_DEPS = """deps = {
  'generic-dep-1': {
    'dep_type': 'gcs',
    'bucket': 'generic-deps-bucket-1',
    'objects': [
      {
        'object_name': 'no-update',
        'sha256sum': 'deadbeef1',
        'size_bytes': 1,
        'generation': 1,
      },
      {
        'object_name': 'has-update-2',
        'sha256sum': 'deadbeef2',
        'size_bytes': 2,
        'generation': 2,
      },
    ],
  },
}"""

DEFAULT_CHROMIUM_DEPS = """deps = {
  'src/generic-dep-1': {
    'dep_type': 'gcs',
    'bucket': 'generic-deps-bucket-1',
    'objects': [
      {
        'object_name': 'no-update',
        'sha256sum': 'deadbeef1',
        'size_bytes': 1,
        'generation': 1,
      },
      {
        'object_name': 'has-update-4',
        'sha256sum': 'deadbeef4',
        'size_bytes': 4,
        'generation': 4,
      },
      {
        'object_name': 'additional-3',
        'sha256sum': 'deadbeef3',
        'size_bytes': 3,
        'generation': 3,
      },
    ],
  },
}"""

CLANG_V8_DEPS = """deps = {
  'third_party/llvm-build/Release+Asserts': {
    'dep_type': 'gcs',
    'bucket': 'chromium-browser-clang',
    'objects': [
      {
        'object_name': 'Linux_x64/llvm-code-coverage-llvmorg-19-init-OLDVERSION.tar.xz',
        'sha256sum': 'sum-4',
        'size_bytes': 4,
        'generation': 14,
        'condition': 'host_os == "linux" and checkout_clang_coverage_tools',
      },
      {
        'object_name': 'Linux_x64/clang-llvmorg-19-init-STABLEVERSION.tar.xz',
        'sha256sum': 'sum-1',
        'size_bytes': 1,
        'generation': 11,
        'condition': 'host_os == "linux"',
      },
    ]
  },
}"""

CLANG_CHROMIUM_DEPS = """deps = {
  'src/third_party/llvm-build/Release+Asserts': {
    'dep_type': 'gcs',
    'bucket': 'chromium-browser-clang',
    'condition': 'not llvm_force_head_revision',
    'objects': [
      {
        'object_name': 'Linux_x64/clang-llvmorg-19-init-STABLEVERSION.tar.xz',
        'sha256sum': 'sum-1',
        'size_bytes': 1,
        'generation': 11,
        'condition': 'host_os == "linux" and non_git_source',
      },
      {
        'object_name': 'Linux_x64/clang-tidy-llvmorg-19-init-10646-g084e2b53-57.tar.xz',
        'sha256sum': 'sum-2',
        'size_bytes': 2,
        'generation': 12,
        'condition': 'host_os == "linux" and checkout_clang_tidy and non_git_source',
      },
      {
        'object_name': 'Linux_x64/llvm-code-coverage-llvmorg-19-init-NEWVERSION.tar.xz',
        'sha256sum': 'sum-3',
        'size_bytes': 3,
        'generation': 13,
        'condition': 'host_os == "linux" and checkout_clang_coverage_tools and non_git_source',
      },
    ],
  },
  'src/third_party/rust-toolchain': {
    'dep_type': 'gcs',
    'bucket': 'chromium-browser-clang',
    'objects': [
      {
        'object_name': 'Linux_x64/rust-toolchain-32dd3795bce8b347fda786529cf5e42a813e0b7d-2-llvmorg-19-init-10646-g084e2b53.tar.xz',
        'sha256sum': 'sum-4',
        'size_bytes': 4,
        'generation': 14,
        'condition': 'host_os == "linux" and non_git_source',
      },
    ],
  },
}"""

COLLIDING_V8_DEPS = """deps = {
  'third_party/llvm-libclang': {
    'dep_type': 'gcs',
    'bucket': 'chromium-browser-clang',
    'objects': [
      {
        'object_name': 'Linux_x64/rust-libclang-OLDVERSION.tar.xz',
        'sha256sum': 'sum-libclang-old',
        'size_bytes': 100,
        'generation': 10,
      },
    ],
  },
  'third_party/rust-toolchain': {
    'dep_type': 'gcs',
    'bucket': 'chromium-browser-clang',
    'objects': [
      {
        'object_name': 'Linux_x64/rust-toolchain-OLDVERSION.tar.xz',
        'sha256sum': 'sum-rust-old',
        'size_bytes': 200,
        'generation': 20,
      },
    ],
  },
}"""

COLLIDING_CHROMIUM_DEPS = """deps = {
  'src/third_party/llvm-libclang': {
    'dep_type': 'gcs',
    'bucket': 'chromium-browser-clang',
    'objects': [
      {
        'object_name': 'Linux_x64/rust-libclang-NEWVERSION.tar.xz',
        'sha256sum': 'sum-libclang-new',
        'size_bytes': 101,
        'generation': 11,
      },
    ],
  },
  'src/third_party/rust-toolchain': {
    'dep_type': 'gcs',
    'bucket': 'chromium-browser-clang',
    'objects': [
      {
        'object_name': 'Linux_x64/rust-toolchain-NEWVERSION.tar.xz',
        'sha256sum': 'sum-rust-new',
        'size_bytes': 201,
        'generation': 21,
      },
    ],
  },
}"""


def RunSteps(api: DEPS):
  # Add defaults
  autoroller_config = {
      'show_commit_log': False,
      'subject': 'Generic deps update',
      'manual_roll_reviewers': ['maik@example.com'],
  }

  update_result = api.v8_auto_roller.setup_target(
      'v8',
      'https://chromium.googlesource.com/v8/v8',
  )
  source_dir = update_result.source_root.path
  clm = api.v8_auto_roller.build_cl_manager(source_dir)
  api.v8_auto_roller.regular_roll(autoroller_config, clm, source_dir)

  return api.v8_auto_roller.report_result()


def GenTests(api: TEST_DEPS):

  def test(name, chromium_deps, v8_deps, *expectations):
    return api.test(
        name,
        api.override_step_data(
            'Find updated deps.Read v8/DEPS',
            api.file.read_text(v8_deps),
        ),
        api.override_step_data(
            'Find updated deps.Read src/DEPS',
            api.file.read_text(chromium_deps),
        ),
        *expectations,
        api.post_process(DropExpectation),
    )

  yield test(
      'update gcs deps',
      DEFAULT_CHROMIUM_DEPS,
      DEFAULT_V8_DEPS,
      api.post_process(MustRun,
                       'Update trusted deps.gclient setdep generic-dep-1'),
  )

  yield test(
      'update clang deps', CLANG_CHROMIUM_DEPS, CLANG_V8_DEPS,
      api.post_process(
          StepCommandContains,
          'Update trusted deps.gclient setdep third_party_llvm-build_Release+Asserts',
          [
              'setdep', '-r',
              'third_party/llvm-build/Release+Asserts@Linux_x64/llvm-code-coverage-llvmorg-19-init-NEWVERSION.tar.xz,sum-3,3,13?Linux_x64/clang-llvmorg-19-init-STABLEVERSION.tar.xz,sum-1,1,11'
          ],
      ))

  yield test(
      'fail to find matching source dep',
      "deps = {}",
      DEFAULT_V8_DEPS,
      api.expect_exception('NotImplementedError'),
  )

  yield test(
      'prevent colliding gcs deps',
      COLLIDING_CHROMIUM_DEPS,
      COLLIDING_V8_DEPS,
      api.post_process(
          StepCommandContains,
          'Update trusted deps.gclient setdep third_party_llvm-libclang',
          [
              'setdep', '-r',
              'third_party/llvm-libclang@Linux_x64/rust-libclang-NEWVERSION.tar.xz,sum-libclang-new,101,11'
          ],
      ),
      api.post_process(
          StepCommandContains,
          'Update trusted deps.gclient setdep third_party_rust-toolchain',
          [
              'setdep', '-r',
              'third_party/rust-toolchain@Linux_x64/rust-toolchain-NEWVERSION.tar.xz,sum-rust-new,201,21'
          ],
      ),
  )
