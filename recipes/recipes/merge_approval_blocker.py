# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Recipe for merge approval blocker.

This recipe should block not approved CLs from landing to the release branch.
If CL needs to be cherry-picked to the relevant release branch, it should
be merge approved in related bug with value `approved-{branch-num}` in
custom field `merge`. More info in go/merge-approval-block.
"""

import re

from recipe_engine import post_process, recipe_api
from PB.recipes.build.merge_approval_blocker import InputProperties

PROPERTIES = InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.depot_tools import gerrit, tryserver
from RECIPE_MODULES.recipe_engine import properties, step


@dataclass
class DEPS(RecipeScriptApi):
  gerrit: gerrit.API
  properties: properties.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  gerrit: gerrit.TEST_API
  properties: properties.TEST_API


def get_cl_description(
  api: DEPS,
  properties: InputProperties,
) -> str:
  """Return CL description from gerrit API

  Args:
    api: recipe_api.RecipeApi object.
    properties: InputProperties object containing the input properties.

  Returns:
    Description string
  """
  # TODO(crbug.com/359821551): - Iterative steps to implement merge blocker.
  # We will be using api.tryserver.get_change_description
  # instead of api.gerrit.get_change_description.
  # description = api.tryserver.get_change_description()
  description = api.gerrit.get_change_description(
    properties.host,
    properties.change,
    properties.patchset,
  )
  return description


def get_bug_ids(text: str) -> list[int]:
  """Return list of bug ids from CL description.

  If text has pattern like 'Bug: 123456', then return list of bug ids.
  Otherwise, return empty list.

  Args:
    text: String with CL description.

  Returns:
    List of integer bug ids.
  """
  pattern = r'\bBug:\s*(\d+(?:,\s*\d+)*)'
  match = re.search(pattern, text)
  if not match:
    return []
  res = match.group(1)
  return list(map(int, res.split(',')))


def get_branch(
  api: DEPS,
  properties: InputProperties,
) -> str:
  """Return branch from the CL

  Args:
    api: recipe_api.RecipeApi object.
    properties: InputProperties object containing the input properties.

  Returns:
    Branch string
  """
  # TODO(crbug.com/359821551): - Iterative steps to implement merge blocker.
  # We will be using api.tryserver.gerrit_change_target_ref
  # instead of api.gerrit.get_revision_info.
  # branch = api.tryserver.gerrit_change_target_ref
  branch = api.gerrit.get_revision_info(
    properties.host,
    properties.change,
    properties.patchset,
  ).get('branch', '')
  return branch


# TODO(crbug.com/359821551): - Iterative steps to implement merge blocker.
def RunSteps(api: DEPS, properties):
  description = get_cl_description(api, properties)
  api.step(
    'Get CL Description',
    ['echo', description],
  )

  with api.step.nest('Get Bug IDs'):
    get_bug_ids(description)

  with api.step.nest('Get Branch'):
    get_branch(api, properties)


def GenTests(api: TEST_DEPS):
  yield api.test(
    'exist_cl_description',
    api.properties(
      host='https://chromium-review.googlesource.com',
      change=5772719,
      patchset=2,
    ),
    api.step_data(
      'gerrit changes',
      api.gerrit.get_one_change_response_data(
        revisions={
          '184ebe53805e102605d11f6b143486d15c23a09c': {
            '_number': '2',
            'commit': {
              'message': 'Bug: 123456',
            },
          },
        },
      ),
    ),
    api.post_process(
      post_process.StepCommandRE,
      'Get CL Description',
      ['echo', 'Bug: 123456'],
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'no_cl_description',
    api.properties(
      host='https://chromium-review.googlesource.com',
      change=1,
      patchset=1,
    ),
    api.post_process(post_process.DropExpectation),
  )
