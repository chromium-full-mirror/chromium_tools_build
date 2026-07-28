# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
    'chromium',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/git',
    'recipe_engine/context',
    'recipe_engine/file',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'v8',
]

def RunSteps(api):
  api.gclient.set_config('v8')
  update_result = api.v8.checkout(with_branch_heads=True)

  source_dir = update_result.source_root.path
  with api.context(cwd=source_dir), api.depot_tools.on_path():
    branches = api.v8.latest_branches()
    assert branches, "No branches found!"
    last_version = branches[0]
    api.step('Last branch %s' % api.v8.version_num2str(last_version), [])

    main_version_obj = api.v8.read_version_from_ref(source_dir, 'main', 'main')
    main_version = api.v8.version_from_text(str(main_version_obj))

    if main_version == last_version:
      with api.step.nest('New branch detected'):
        next_main_version_obj = main_version_obj.with_incremented_minor()
        api.v8.update_version_cl(
            source_dir,
            'main',
            next_main_version_obj,
            push_account=api.v8.V8_CI_AUTOROLL_BUILDER,
            bot_commit=True,
        )
    else:
      api.step('No new branch detected', [])


def GenTests(api):

  def stdout(step_name, text):
    return api.override_step_data(
        step_name, api.raw_io.stream_output_text(text, stream='stdout'))

  yield (api.test("no new branch", status='SUCCESS') +
         stdout('last branches', 'branch-heads/10.2\n'
                'branch-heads/10.1\n'
                'branch-heads/10.0') +
         api.v8.version_file(4, 'main', major=10, minor=3))

  yield (api.test("new branch", status='SUCCESS') + stdout(
      'last branches', 'branch-heads/10.0\n'
      'branch-heads/9.9\n'
      'branch-heads/9.8\n'
      'branch-heads/9.8') + api.v8.version_file(4, 'main', major=10, minor=0) +
         api.v8.version_file(
             4, 'latest', prefix='New branch detected.', major=10, minor=0) +
         stdout('New branch detected.git cl', 'Issue number: 2 '
                '(https://review.source.com/2)'))
