# Copyright (c) 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from PB.recipe_engine import result as result_pb
from PB.recipes.build.reclient_config_deploy_check import tester as tester_pb

PROPERTIES = tester_pb.InputProperties

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.depot_tools import (
  bot_update,
  gclient,
  git,
  tryserver,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  path,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  context: context.API
  gclient: gclient.API
  git: git.API
  path: path.API
  properties: properties.API
  step: step.API
  tryserver: tryserver.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  properties: properties.TEST_API


def _result(status, header, elements, footer=None):
  summary = [header, '']
  summary.extend('* {}'.format(e) for e in elements)
  if footer:
    summary.extend(['', footer])
  return result_pb.RawResult(status=status, summary_markdown='\n'.join(summary))


def RunSteps(api: DEPS, properties):
  gclient_config = api.gclient.make_config()
  s = gclient_config.solutions.add()
  s.url = api.tryserver.gerrit_change_repo_url
  s.name = s.url.rsplit('/', 1)[-1]
  gclient_config.got_revision_mapping[s.name] = 'got_revision'

  with api.context(cwd=api.path.cache_dir / 'builder'):
    update_result = api.bot_update.ensure_checkout(
      patch=True, gclient_config=gclient_config
    )

  repo_path = update_result.source_root.path

  bad_reclient_configs = []
  with api.context(cwd=repo_path):
    fetch_script = repo_path / properties.fetch_script
    for p in properties.rbe_project:
      with api.step.nest(p.name):
        fetch_cmd = [fetch_script, '--rbe_project', p.name]
        try:
          api.step('fetch configs', fetch_cmd, infra_step=True)

          with api.step.nest('verify'):
            for cfg in p.cfg_file:
              cfg = repo_path / cfg
              # Mock the cfg files as existing for the purposes of testing.
              if api.properties.get('mock_cfgs', False):
                api.path.mock_add_paths(cfg)
              if not api.path.exists(cfg):
                bad_reclient_configs.append(p.name + ": " + str(cfg))
        except api.step.StepFailure:
          bad_reclient_configs.append(p.name + ": failure during config fetch")

        with api.step.nest('restore'):
          api.git('restore', '.', infra_step=True)
          api.git('clean', '-f', infra_step=True)

  if bad_reclient_configs:
    return _result(
      status=common_pb.FAILURE,
      elements=bad_reclient_configs,
      header='The following reclient configs were missing:',
      footer='See steps for more information',
    )


def GenTests(api: TEST_DEPS):
  yield api.test(
    'basic',
    api.buildbucket.try_build(),
    api.properties(
      tester_pb.InputProperties(
        fetch_script='fetch-script',
        rbe_project=[
          tester_pb.ProjectConfigVerification(
            name='rbe-project-1',
            cfg_file=[
              'rewrapper-linux.cfg',
              'rewrapper-win.cfg',
            ],
          )
        ],
      )
    ),
    api.properties(mock_cfgs=True),
    api.post_check(
      post_process.MustRun,
      'rbe-project-1.fetch configs',
      'rbe-project-1.verify',
    ),
  )

  yield api.test(
    'missing configs',
    api.buildbucket.try_build(),
    api.properties(
      tester_pb.InputProperties(
        fetch_script='fetch-script',
        rbe_project=[
          tester_pb.ProjectConfigVerification(
            name='rbe-project-1', cfg_file=['rewrapper-linux.cfg']
          )
        ],
      )
    ),
    api.properties(mock_cfgs=False),
    api.post_check(
      post_process.MustRun,
      'rbe-project-1.fetch configs',
      'rbe-project-1.verify',
    ),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'failed fetch',
    api.buildbucket.try_build(),
    api.properties(
      tester_pb.InputProperties(
        fetch_script='fetch-script',
        rbe_project=[
          tester_pb.ProjectConfigVerification(
            name='rbe-project-1', cfg_file=['rewrapper-linux.cfg']
          )
        ],
      )
    ),
    api.properties(mock_cfgs=False),
    api.step_data('rbe-project-1.fetch configs', retcode=1),
    api.post_check(post_process.StepException, 'rbe-project-1'),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
