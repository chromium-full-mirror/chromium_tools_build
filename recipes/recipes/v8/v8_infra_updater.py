# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

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
      # Safety checks
      if len(active_branches) > 5:
        raise api.step.StepFailure(
            f'Too many active branches: {len(active_branches)} (max 5)')

      added = [b for b in active_branches if b not in current_branches]
      removed = [b for b in current_branches if b not in active_branches]

      if added:
        if len(added) > 1:
          raise api.step.StepFailure(
              f'Too many branches added: {", ".join(added)} (max 1)')
        new_branch = added[0]
        if new_branch != active_branches[0]:
          raise api.step.StepFailure(
              f'Added branch {new_branch} is not the newest')
        if current_branches:
          new_v = api.v8.version_from_text(new_branch)
          old_v = api.v8.version_from_text(current_branches[0])
          if new_v != old_v + 1:
            raise api.step.StepFailure(
                f'New branch {new_branch} is not 0.1 higher than '
                f'{current_branches[0]}')

      if removed:
        if current_branches[0] in removed:
          raise api.step.StepFailure(
              f'Newest branch {current_branches[0]} was removed')

      with api.step.nest('New branch detected'):
        definitions = api.v8.update_active_branches(active_branches)
        api.v8.update_infra_config(source_dir, definitions)
    else:
      api.step('No new branch detected', [])


def GenTests(api):

  def fake_milestones(branches=None):
    if branches is None:
      branches = [{
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
      }]
    return api.url.json(
        'GET https://chromiumdash.appspot.com/fetch_milestones?'
        'num=0&only_active=true', branches)

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

  yield (api.test("too many branches", status='FAILURE') + fake_milestones([{
      'milestone': 126,
      'v8_branch': '12.6'
  }, {
      'milestone': 125,
      'v8_branch': '12.5'
  }, {
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
  }]) + stdout('Read branch definitions', 'ACTIVE_BRANCHES = ["12.5"]') +
         api.post_process(post_process.SummaryMarkdown,
                          'Too many active branches: 6 (max 5)') +
         api.post_process(post_process.DropExpectation))

  yield (api.test("too many branches added", status='FAILURE') +
         fake_milestones() + stdout('Read branch definitions',
                                    'ACTIVE_BRANCHES = ["12.2", "12.1"]') +
         api.post_process(post_process.SummaryMarkdown,
                          'Too many branches added: 12.4, 12.3 (max 1)') +
         api.post_process(post_process.DropExpectation))

  yield (api.test("added branch not newest", status='FAILURE') +
         fake_milestones([{
             'milestone': 125,
             'v8_branch': '12.5'
         }, {
             'milestone': 123,
             'v8_branch': '12.3'
         }]) + stdout('Read branch definitions', 'ACTIVE_BRANCHES = ["12.5"]') +
         api.post_process(post_process.SummaryMarkdown,
                          'Added branch 12.3 is not the newest') +
         api.post_process(post_process.DropExpectation))

  yield (api.test("new branch not 0.1 higher", status='FAILURE') +
         fake_milestones([{
             'milestone': 125,
             'v8_branch': '12.5'
         }]) + stdout('Read branch definitions', 'ACTIVE_BRANCHES = ["12.3"]') +
         api.post_process(post_process.SummaryMarkdown,
                          'New branch 12.5 is not 0.1 higher than 12.3') +
         api.post_process(post_process.DropExpectation))

  yield (
      api.test("newest branch removed", status='FAILURE') + fake_milestones([{
          'milestone': 123,
          'v8_branch': '12.3'
      }]) +
      stdout('Read branch definitions', 'ACTIVE_BRANCHES = ["12.4", "12.3"]') +
      api.post_process(post_process.SummaryMarkdown,
                       'Newest branch 12.4 was removed') +
      api.post_process(post_process.DropExpectation))
