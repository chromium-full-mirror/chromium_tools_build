# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, v8
from RECIPE_MODULES.depot_tools import (
    depot_tools,
    gclient,
    gerrit,
    git,
)
from RECIPE_MODULES.recipe_engine import (
    context,
    file,
    raw_io,
    step,
    url,
)


@dataclass
class DEPS(RecipeScriptApi):
  chromium: chromium.API
  context: context.API
  depot_tools: depot_tools.API
  file: file.API
  gclient: gclient.API
  gerrit: gerrit.API
  git: git.API
  raw_io: raw_io.API
  step: step.API
  url: url.API
  v8: v8.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  gerrit: gerrit.TEST_API
  raw_io: raw_io.TEST_API
  url: url.TEST_API

HASHTAG = 'v8-infra-update'


def RunSteps(api: DEPS):
  api.gclient.set_config('v8')
  update_result = api.v8.checkout(with_branch_heads=True)

  cls = api.gerrit.get_changes(
      'https://chromium-review.googlesource.com',
      query_params=[
          ('status', 'open'),
          ('hashtag', HASHTAG),
      ],
      limit=1,
      step_test_data=api.gerrit.test_api.get_empty_changes_response_data,
      name='Check for existing CLs',
  )
  if cls:
    cl = cls[0]
    cl_url = f"https://chromium-review.googlesource.com/c/{cl['_number']}"
    step_result = api.step('Existing CL found', [])
    step_result.presentation.status = api.step.FAILURE
    step_result.presentation.links[cl_url] = cl_url
    raise api.step.StepFailure(
        f'Found open CL with hashtag {HASHTAG}: {cl_url}')

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
        if len(active_branches) > 1 and new_branch == active_branches[-1]:
          raise api.step.StepFailure(f'Added branch {new_branch} is the oldest')

      if removed:
        if current_branches[0] in removed:
          raise api.step.StepFailure(
              f'Newest branch {current_branches[0]} was removed')

      with api.step.nest('New branch detected'):
        definitions = api.v8.update_active_branches(active_branches)
        api.v8.update_infra_config(source_dir, definitions, hashtag=HASHTAG)
    else:
      api.step('No new branch detected', [])


def GenTests(api: TEST_DEPS):

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

  yield (api.test("added branch is oldest", status='FAILURE') +
         fake_milestones([{
             'milestone': 125,
             'v8_branch': '12.5'
         }, {
             'milestone': 123,
             'v8_branch': '12.3'
         }]) + stdout('Read branch definitions', 'ACTIVE_BRANCHES = ["12.5"]') +
         api.post_process(post_process.SummaryMarkdown,
                          'Added branch 12.3 is the oldest') +
         api.post_process(post_process.DropExpectation))

  yield (api.test("new branch in the middle", status='SUCCESS') +
         fake_milestones([{
             'milestone': 125,
             'v8_branch': '12.5'
         }, {
             'milestone': 124,
             'v8_branch': '12.4'
         }, {
             'milestone': 123,
             'v8_branch': '12.3'
         }]) + stdout('Read branch definitions',
                      'ACTIVE_BRANCHES = ["12.5", "12.3"]') +
         stdout('New branch detected.Update infra/config.git cl (2)',
                'Issue number: 3 '
                '(https://review.source.com/3)') +
         api.post_process(post_process.DropExpectation))

  yield (api.test("new branch replaces all", status='FAILURE') +
         fake_milestones([{
             'milestone': 125,
             'v8_branch': '12.5'
         }]) + stdout('Read branch definitions', 'ACTIVE_BRANCHES = ["12.3"]') +
         api.post_process(post_process.SummaryMarkdown,
                          'Newest branch 12.3 was removed') +
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

  yield (api.test(
      "existing cl found", status='FAILURE'
  ) + api.override_step_data(
      'gerrit Check for existing CLs',
      api.gerrit.get_one_change_response_data(
          change_number=123456,
          patchset=1,
      )
  ) + api.post_process(
      post_process.SummaryMarkdown,
      'Found open CL with hashtag v8-infra-update: https://chromium-review.googlesource.com/c/123456'
  ) + api.post_process(post_process.DropExpectation))
