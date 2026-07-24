# Copyright 2026 The Chromium Authors
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
    'recipe_engine/url',
    'v8',
]


def RunSteps(api):
  api.gclient.set_config('v8')
  update_result = api.v8.checkout(with_branch_heads=True)

  source_dir = update_result.source_root.path
  with api.context(cwd=source_dir), api.depot_tools.on_path():
    active_branches = api.v8.get_active_branches()
    assert active_branches, "No active branches found from ChromiumDash!"

    api.v8.git_output('checkout', 'infra/config')
    api.v8.git_output('pull')

    definitions = api.v8.git_output(
        'show', 'HEAD:definitions.star', name='Read branch definitions')

    current_branches = api.v8.infer_active_branches(definitions)
    if active_branches != current_branches:
      with api.step.nest('New branch detected'):
        definitions = api.v8.update_active_branches(active_branches)
        api.v8.update_infra_config(source_dir, definitions)
    else:
      api.step('No new branch detected', [])


def GenTests(api):

  def fake_milestones():
    return api.url.json(
        'GET https://chromiumdash.appspot.com/fetch_milestones?'
        'num=0&only_active=true', [{
            'milestone': 124,
            'v8_branch': '12.4'
        }, {
            'milestone': 123,
            'v8_branch': '12.3'
        }, {
            'milestone': 122,
            'v8_branch': '12.2'
        }, {
            'milestone': 121,
            'v8_branch': '12.1'
        }])

  def stdout(step_name, text):
    return api.override_step_data(
        step_name, api.raw_io.stream_output_text(text, stream='stdout'))

  yield (api.test("no new branch", status='SUCCESS') + fake_milestones() +
         stdout('Read branch definitions', 'ACTIVE_BRANCHES = ['
                '"12.4", "12.3", "12.2", "12.1"]'))

  yield (api.test("new branch", status='SUCCESS') + fake_milestones() +
         stdout('Read branch definitions', 'ACTIVE_BRANCHES = ['
                '"12.3", "12.2", "12.1", "12.0"]') +
         stdout('New branch detected.Update infra/config.git cl (2)',
                'Issue number: 3 '
                '(https://review.source.com/3)'))
