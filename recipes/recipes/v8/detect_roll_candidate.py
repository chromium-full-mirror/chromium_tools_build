# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import re

from recipe_engine.post_process import (
    DoesNotRun, DropExpectation, StepCommandContains, StepTextEquals)

from RECIPE_MODULES.build.v8.v8version import choose_revision_to_roll

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import v8
from RECIPE_MODULES.depot_tools import (
    bot_update,
    gclient,
    git,
    gitiles,
)
from RECIPE_MODULES.recipe_engine import (
    context,
    file,
    path,
    raw_io,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  context: context.API
  file: file.API
  gclient: gclient.API
  git: git.API
  gitiles: gitiles.API
  path: path.API
  raw_io: raw_io.API
  step: step.API
  v8: v8.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  file: file.TEST_API
  gclient: gclient.TEST_API
  git: git.TEST_API
  gitiles: gitiles.TEST_API
  path: path.TEST_API
  raw_io: raw_io.TEST_API
  step: step.TEST_API
  v8: v8.TEST_API

TEST_DEPS_FILE = """
vars = {
  'chromium_git': 'https://chromium.googlesource.com',
  'v8_revision': '%s',
}

deps = {
  'src/v8':
    Var('chromium_git') + '/v8/v8.git' + '@' +  Var('v8_revision'),
}
"""

V8_VERSION_RE = re.compile(r'^\d+\.\d+\.\d+(?:\.\d+)?$')


def get_last_v8_revision(api):
  """Retrieve the last V8 revision in Chromium from gitiles."""
  deps = api.gitiles.download_file(
      'https://chromium.googlesource.com/chromium/src',
      'DEPS',
      branch='refs/heads/main',
      step_test_data=lambda: api.gitiles.test_api.make_encoded_file(
          TEST_DEPS_FILE % 'deadbeef'),
  )

  deps_file = api.path.mkdtemp('gitiles') / 'DEPS'
  api.file.write_text('gitiles', deps_file, deps)
  revision = api.gclient(
      'get gitiles deps',
      ['getdep', '--var=v8_revision', f'--deps-file={deps_file}'],
      stdout=api.raw_io.output_text(),
  ).stdout.strip()
  api.step.active_result.presentation.logs['revision'] = [revision]
  return revision


def get_v8_tag(api, revision):
  """Returns the V8 version tag associated with a revision or None."""
  tags = api.v8.git_output('tag', '--points-at', revision).split('\n')
  return next((tag for tag in tags if V8_VERSION_RE.match(tag)), None)


def get_next_v8_revision(api, last_v8_revision):
  """Choose the next newest viable V8 revision to roll.

  We override with the last if the algorithm determined that a new
  version isn't ready yet. If, e.g., a new patched version is in the
  works, but not ready, this ensures that we won't roll to anything
  else in the meantime.

  Args:
    last_v8_revision: The previously rolled revision.
  """
  with api.step.nest('Choose revision') as parent:
    api.git('fetch', 'origin', '+refs/tags/*:refs/tags/*')

    last_version = get_v8_tag(api, last_v8_revision)
    assert last_version, 'The last rolled v8 revision is not tagged.'

    ref_lines = api.v8.git_output(
        'for-each-ref', '--count=160', '--sort=-committerdate',
        '--format', '%(refname) %(objectname) %(committerdate)',
        'refs/tags/*',
    ).split('\n')
    revision, reason = choose_revision_to_roll(ref_lines, last_version)
    parent.step_text = reason
    return revision or last_v8_revision


def RunSteps(api: DEPS):
  api.gclient.set_config('v8_bare')
  update_result = api.v8.checkout()
  source_dir = update_result.source_root.path

  last_v8_revision = get_last_v8_revision(api)
  with api.context(
      cwd=source_dir,
      env={'DEPOT_TOOLS_UPDATE': '0'},
      env_prefixes={'PATH': [api.v8.depot_tools_path(source_dir)]}):
    next_v8_revision = get_next_v8_revision(api, last_v8_revision)

    api.git(
        'fetch', 'https://chromium.googlesource.com/v8/v8',
        'refs/heads/roll', next_v8_revision)
    api.git(
        'push', 'https://chromium.googlesource.com/v8/v8',
        f'+{next_v8_revision}:refs/heads/roll')


TEST_REF_DATA = """
refs/tags/11.7.10-pgo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2 Fri Jul 7 11:32:02 2023 +0000
refs/tags/11.7.10 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2 Fri Jul 7 11:32:02 2023 +0000
refs/tags/11.7.9-pgo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1 Fri Jul 7 10:32:02 2023 +0000
refs/tags/11.7.9 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1 Fri Jul 7 10:32:02 2023 +0000
"""


def GenTests(api: TEST_DEPS):
  def last_v8_revision():
    return api.override_step_data(
        'gclient get gitiles deps',
        api.raw_io.stream_output_text('deadbeef', stream='stdout'))

  def v8_tag(tag):
    return api.override_step_data(
        'Choose revision.git tag',
        api.raw_io.stream_output_text(f'{tag}-pgo\n{tag}', stream='stdout'))

  def v8_ref_data():
    return api.override_step_data(
        'Choose revision.git for-each-ref',
        api.raw_io.stream_output_text(TEST_REF_DATA, stream='stdout'))

  yield api.test(
      'standard',
      last_v8_revision(),
      v8_tag('11.7.8'),
      v8_ref_data(),
      api.post_process(
          StepTextEquals,
          'Choose revision',
          'found revision to roll: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2'),
      api.post_process(
          StepCommandContains,
          'git push',
          ['+aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2:refs/heads/roll']),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  yield api.test(
      'nothing_new',
      last_v8_revision(),
      v8_tag('11.7.11'),
      v8_ref_data(),
      api.post_process(
          StepTextEquals,
          'Choose revision',
          'found no newer revision than: 11.7.11'),
      api.post_process(
          StepCommandContains,
          'git push',
          ['+deadbeef:refs/heads/roll']),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )
