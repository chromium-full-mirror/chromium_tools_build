# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine.post_process import MustRun, DropExpectation

DEPS = [
    'recipe_engine/file',
    'v8_auto_roller',
]

V8_DEPS = """
vars = {
  'chromium_url': Str('https://chromium.googlesource.com/'),
}

deps = {
  'third_party/icu': Var('chromium_url') + 'chromium/deps/icu.git@364118a1d9da24bb5b770ac3d762ac144d6da5a4'
}
"""

CHROMIUM_DEPS = """
deps = {
  'src/third_party/icu': 'https://chromium.googlesource.com/chromium/deps/icu.git@a622de35ac311c5ad390a7af80724634e5dc61ed'
}
"""


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
      'apply_Var_Str',
      CHROMIUM_DEPS,
      V8_DEPS,
      api.post_process(MustRun,
                       'Update trusted deps.gclient setdep third_party_icu'),
  )
