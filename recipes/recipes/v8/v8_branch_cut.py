# Copyright 2021 The Chromium Authors. All rights reserved.
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
  v8: v8.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  gerrit: gerrit.TEST_API
  raw_io: raw_io.TEST_API
  v8: v8.TEST_API


HASHTAG = "version-auto-update"


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
      f'Found open CL with hashtag {HASHTAG}: {cl_url}'
    )

  source_dir = update_result.source_root.path
  with api.context(cwd=source_dir), api.depot_tools.on_path():
    branches = api.v8.latest_branches()
    assert branches, "No branches found!"
    last_version = branches[0]
    api.step('Last branch %s' % api.v8.version_num2str(last_version), [])

    api.v8.git_output('checkout', 'main')
    api.v8.git_output('pull')

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
          hashtag=HASHTAG,
          use_cq=True,
        )
    else:
      api.step('No new branch detected', [])


def GenTests(api: TEST_DEPS):

  def stdout(step_name, text):
    return api.override_step_data(
      step_name, api.raw_io.stream_output_text(text, stream='stdout')
    )

  yield (
    api.test("no new branch", status='SUCCESS')
    + stdout(
      'last branches', 'branch-heads/10.2\nbranch-heads/10.1\nbranch-heads/10.0'
    )
    + api.v8.version_file(4, 'main', major=10, minor=3)
  )

  yield (
    api.test("new branch", status='SUCCESS')
    + stdout(
      'last branches',
      'branch-heads/10.0\nbranch-heads/9.9\nbranch-heads/9.8\nbranch-heads/9.8',
    )
    + api.v8.version_file(4, 'main', major=10, minor=0)
    + api.v8.version_file(
      4, 'latest', prefix='New branch detected.', major=10, minor=0
    )
    + stdout(
      'New branch detected.git cl',
      'Issue number: 2 (https://review.source.com/2)',
    )
  )

  yield (
    api.test("existing cl found", status='FAILURE')
    + api.override_step_data(
      'gerrit Check for existing CLs',
      api.gerrit.get_one_change_response_data(
        change_number=123456,
        patchset=1,
      ),
    )
    + api.post_process(
      post_process.SummaryMarkdown,
      'Found open CL with hashtag version-auto-update: https://chromium-review.googlesource.com/c/123456',
    )
    + api.post_process(post_process.DropExpectation)
  )
