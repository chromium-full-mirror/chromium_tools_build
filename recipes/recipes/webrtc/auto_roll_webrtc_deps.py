# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_checkout, webrtc
from RECIPE_MODULES.depot_tools import (
  depot_tools,
  gclient,
  gerrit,
  git,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  json,
  runtime,
  step,
  url,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  chromium_checkout: chromium_checkout.API
  context: context.API
  depot_tools: depot_tools.API
  gclient: gclient.API
  gerrit: gerrit.API
  git: git.API
  json: json.API
  runtime: runtime.API
  step: step.API
  url: url.API
  webrtc: webrtc.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  json: json.TEST_API
  runtime: runtime.TEST_API
  url: url.TEST_API


GERRIT_URL = 'https://webrtc-review.googlesource.com'
GERRIT_PROJECT = 'src'


def RunSteps(api: DEPS):
  api.gclient.set_config('webrtc')

  # Make sure the checkout contains all deps for all platforms.
  for os in ['linux', 'android', 'mac', 'ios', 'win', 'unix', 'fuchsia']:
    api.gclient.c.target_os.add(os)

  output = api.url.get_text(
    'https://webrtc-roll-cr-rev-status.appspot.com/status',
    step_name='check roll status',
    default_test_data='1',
  ).output
  api.step.active_result.presentation.logs['output'] = output.splitlines()
  if output.strip() != '1':
    api.step.active_result.presentation.step_text = 'Rolling deactivated'
    return

  api.step.active_result.presentation.step_text = 'Rolling activated'

  update_result = api.chromium_checkout.ensure_checkout()

  source_dir = update_result.source_root.path
  with api.context(cwd=source_dir):
    # TODO(oprypin): Replace with api.service_account.default().get_email()
    # when https://crbug.com/846923 is resolved.
    push_account = 'chromium-webrtc-autoroll@webrtc-ci.iam.gserviceaccount.com'

    # Check for an open auto-roller CL.
    commits = api.gerrit.get_changes(
      GERRIT_URL,
      query_params=[
        ('project', GERRIT_PROJECT),
        ('owner', push_account),
        ('status', 'open'),
      ],
      limit=1,
    )
    if commits:
      cq_commits = api.gerrit.get_changes(
        GERRIT_URL,
        query_params=[
          ('change', commits[0]['_number']),
          ('label', 'Commit-Queue>=1'),
        ],
        limit=1,
      )
      if cq_commits:
        assert cq_commits[0]['_number'] == commits[0]['_number']
        api.step.active_result.presentation.step_text = 'Active rolls found.'
        return

      with api.context(env={'SKIP_GCE_AUTH_FOR_GIT': '1'}):
        with api.depot_tools.on_path():
          api.git('cl', 'set-close', '-i', commits[0]['_number'])
        api.step.active_result.presentation.step_text = (
          'Stale roll found. Abandoned.'
        )

    # Enforce a clean state, and discard any local commits from previous runs.
    api.git('checkout', '-f', 'main')
    api.git('pull', 'origin', 'main')
    api.git('clean', '-ffd')

    # Run the roll script. It will take care of branch creation, modifying DEPS,
    # uploading etc. It will also delete any previous roll branch.
    script_path = source_dir.joinpath(
      'tools_webrtc', 'autoroller', 'roll_deps.py'
    )

    params = ['--clean', '--verbose']
    if api.runtime.is_experimental:
      params.append('--skip-cq')
    else:
      params.append('--cq-over=100')

    cmd = ['vpython3', '-u', script_path] + params
    with api.depot_tools.on_path():
      api.step('autoroll DEPS', cmd)


def GenTests(api: TEST_DEPS):
  base = api.buildbucket.generic_build()

  yield api.test(
    'rolling_activated',
    base,
    api.override_step_data('gerrit changes', api.json.output([])),
  )
  yield api.test(
    'rolling_activated_experimental',
    base,
    api.override_step_data('gerrit changes', api.json.output([])),
    api.runtime(is_experimental=True),
  )
  yield api.test(
    'rolling_deactivated',
    base,
    api.url.text('check roll status', '0'),
  )
  yield api.test(
    'stale_roll',
    base,
    api.override_step_data(
      'gerrit changes', api.json.output([{'_number': '123'}])
    ),
    api.override_step_data('gerrit changes (2)', api.json.output([])),
  )
  yield api.test(
    'previous_roll_in_cq',
    base,
    api.override_step_data(
      'gerrit changes', api.json.output([{'_number': '123'}])
    ),
    api.override_step_data(
      'gerrit changes (2)', api.json.output([{'_number': '123'}])
    ),
  )
