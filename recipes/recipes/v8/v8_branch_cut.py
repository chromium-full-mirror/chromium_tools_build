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

    api.v8.git_output('checkout', 'infra/config')
    api.v8.git_output('pull')

    definitions = api.v8.git_output(
        'show', 'HEAD:definitions.star', name='Read branch definitions')
    beta_version = api.v8.infer_beta_version(definitions)
    if last_version != beta_version:
      with api.step.nest('New branch detected'):
        api.v8.update_main_version(source_dir)
    else:
      api.step('No new branch detected', [])


def GenTests(api):

  def stdout(step_name, text):
    return api.override_step_data(
        step_name, api.raw_io.stream_output_text(text, stream='stdout'))

  yield (api.test("no new branch", status='SUCCESS') +
         stdout('last branches', 'branch-heads/9.9\n'
                'branch-heads/10.1\n'
                'branch-heads/10.2') + stdout(
                    'Read branch definitions', 'versions = {'
                    '"beta": "10.2", "stable": "10.1", "extended": "10.0"}'))

  yield (
      api.test("new branch", status='SUCCESS') + stdout(
          'last branches', 'branch-heads/10.0\n'
          'branch-heads/9.9\n'
          'branch-heads/9.8\n'
          'branch-heads/9.8') + stdout(
              'Read branch definitions', 'versions = {'
              '"beta": "9.9", "stable": "9.8", "extended": "9.8"}') +
      api.v8.version_file(
          4,
          'main',
          prefix='New branch detected.Update on main.',
          major=9,
          minor=9) +
      stdout('New branch detected.Update on main.git cl (2)', 'Issue number: 2 '
             '(https://review.source.com/2)'))
