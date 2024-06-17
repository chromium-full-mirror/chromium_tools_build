# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import DoesNotRun, MustRun, DropExpectation

DEPS = [
    'recipe_engine/file',
    'v8_auto_roller',
]

V8_DEPS = """deps = {
  'llvm-build': {
    'dep_type': 'gcs',
    'bucket': 'chromium-browser-clang',
    'objects': [
      {
        'object_name': 'Linux_x64/clang-llvmorg-19-init-10646-g084e2b53-7.tar.xz',
        'sha256sum': 'd0464562eae265f314068f43bdd2c3f0781b13e462eebd7f5f82135cff673409',
        'size_bytes': 50747404,
        'generation': 1717404567139403,
        'condition': 'host_os == "linux"',
      },
      {
        'object_name': 'Linux_x64/clang-tidy-llvmorg-19-init-10646-g084e2b53-57.tar.xz',
        'sha256sum': '0ca2bb8311f1abcb80ba0d8c9a97c1375a5e14ff1c7920633d10cf3137fa272b',
        'size_bytes': 12898000,
        'generation': 1717767136015811,
        'condition': 'host_os == "linux" and checkout_clang_tidy',
      },
    ],
  },
}"""

CHROMIUM_DEPS = """deps = {
  'src/llvm-build': {
    'dep_type': 'gcs',
    'bucket': 'chromium-browser-clang',
    'objects': [
      # Already up-to-date.
      {
        'object_name': 'Linux_x64/clang-llvmorg-19-init-10646-g084e2b53-7.tar.xz',
        'sha256sum': 'd0464562eae265f314068f43bdd2c3f0781b13e462eebd7f5f82135cff673409',
        'size_bytes': 50747404,
        'generation': 1717404567139403,
        'condition': 'host_os == "linux" and non_git_source',
      },
      # Newer dependency.
      {
        'object_name': 'Linux_x64/clang-tidy-llvmorg-19-init-10646-g084e2b53-7.tar.xz',
        'sha256sum': '5996cd916df018f07ba371971b7327299a4c0a66e02c8cfb1647687f201fcde3',
        'size_bytes': 12918772,
        'generation': 1717404567246693,
        'condition': 'host_os == "linux" and checkout_clang_tidy and non_git_source',
      },
      # Additional dependency - does not exist in V8.
      {
        'object_name': 'Linux_x64/clangd-llvmorg-19-init-10646-g084e2b53-7.tar.xz',
        'sha256sum': 'aaac42bf958caa55ee954117adc1539a6416beea5a85c0ee63f0f3470479d019',
        'size_bytes': 13290628,
        'generation': 1717404567369813,
        'condition': 'host_os == "linux" and checkout_clangd and non_git_source',
      },
    ],
  },
}"""


def RunSteps(api):
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


def GenTests(api):

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
      CHROMIUM_DEPS,
      V8_DEPS,
      api.post_process(MustRun,
                       'Update trusted deps.gclient setdep llvm-build'),
  )

  yield test(
      'fail to find matching source dep',
      "deps = {}",
      V8_DEPS,
      api.expect_exception('NotImplementedError'),
  )
