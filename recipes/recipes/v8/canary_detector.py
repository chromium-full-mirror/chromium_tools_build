# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
Recipe that gets the deps entry of a component in the latest chrome canary
release. The revision in that deps entry is pushed to a ref.
"""

import re
from distutils.version import StrictVersion
from recipe_engine.post_process import DoesNotRun, DropExpectation, MustRun

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import builder_group, v8
from RECIPE_MODULES.depot_tools import bot_update, gclient, git
from RECIPE_MODULES.recipe_engine import (
    buildbucket,
    context,
    file,
    json,
    path,
    properties,
    raw_io,
    step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  context: context.API
  file: file.API
  gclient: gclient.API
  git: git.API
  json: json.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  step: step.API
  v8: v8.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  context: context.TEST_API
  gclient: gclient.TEST_API
  git: git.TEST_API
  path: path.TEST_API
  raw_io: raw_io.TEST_API
  step: step.TEST_API
  v8: v8.TEST_API

DEPS_KEY = 'src/v8'
REPO = 'https://chromium.googlesource.com/v8/v8'

CANARY_REF = 'refs/heads/canary'

VERSION_RE = re.compile(r'^\d+\.\d+\.\d+$')


def GetHeads(api, repo):
  """Return all branch heads and the current revision of the remote repository.

  Returns:
      Dict[str, str]: Branch name to head revision, e.g.
          main -> 6072179d980e7ca68796ccdb37940a16f8d456e9
  """
  with api.context(cwd=api.path.mkdtemp()):
    step_result = api.git(
        'ls-remote',
        '--heads',
        repo,
        name='git ls-remote --heads %s' % repo,
        stdout=api.raw_io.output_text(),
    )
    refs = step_result.stdout.strip().split("\n")
    step_result.presentation.logs['heads'] = refs

    heads_by_name = dict()
    for ref in refs:
      revision, name = ref.split("\t")
      name = re.sub(r"^refs/heads/", "", name)
      heads_by_name[name] = revision

    return heads_by_name


def PushRef(api, repo, hsh):
  api.gclient.set_config('v8')
  update_result = api.v8.checkout()

  with api.context(cwd=update_result.source_root.path):
    api.git('update-ref', CANARY_REF, hsh)
    api.git('push', repo, '-f', '%s:%s' % (CANARY_REF, CANARY_REF))


def LogStep(api, text):
  api.step('log', ['echo', text])


def GetCanaryVersion(revisions_by_head):
  """Return the revision for the next canary version."""
  # Filter all heads which consist of a three-partioned version, e.g. 1.2.3
  versions = filter(VERSION_RE.match, revisions_by_head.keys())

  # Sort versions and select the most recent one
  recent_version = sorted(versions, key=StrictVersion)[-1]

  return revisions_by_head[recent_version]


def RunSteps(api: DEPS):
  repo = api.properties.get('repo', REPO)

  revisions_by_head = GetHeads(api, repo)
  current_canary = revisions_by_head.get("canary")
  new_canary = GetCanaryVersion(revisions_by_head)

  if new_canary != current_canary:
    PushRef(api, repo, new_canary)
  else:
    LogStep(api, 'There is no new canary.')


def GenTests(api: TEST_DEPS):

  def get_remote_refs(canary_rev):
    return "\n".join([
        "0eca9b90ee3689f6cbf93e01b5e0fc480175ce42\trefs/heads/9.9.24",
        "8d84dc03b82aafce14f043b523ea94b14fdf89ca\trefs/heads/9.9.9",
        "%s\trefs/heads/canary" % canary_rev,
        "6c30d63ab9c9cfef8dd01cd70406b4f70a2f45e1\trefs/heads/main",
    ]) + "\n"

  def generate_test(name, current_canary, *expectations):
    return api.test(
        name,
        api.buildbucket.ci_build(
            project='v8',
            git_repo=REPO,
            builder='V8 canary detector',
        ),
        api.override_step_data(
            'git ls-remote --heads %s' % REPO,
            api.raw_io.stream_output_text(
                get_remote_refs(current_canary), stream='stdout'),
        ),
        *expectations,
        api.post_process(DropExpectation),
    )

  yield generate_test(
      'new_canary',
      '8d84dc03b82aafce14f043b523ea94b14fdf89ca',
      api.post_process(MustRun, 'git update-ref'),
      api.post_process(MustRun, 'git push'),
  )
  yield generate_test(
      'same_canary',
      '0eca9b90ee3689f6cbf93e01b5e0fc480175ce42',
      api.post_process(DoesNotRun, 'git update-ref'),
      api.post_process(DoesNotRun, 'git push'),
  )
