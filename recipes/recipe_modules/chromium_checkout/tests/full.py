# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

from recipe_engine.post_process import (DoesNotRun, DropExpectation, MustRun,
                                        PropertyEquals, StepCommandContains,
                                        StepSuccess)
from recipe_engine.recipe_api import Property

from RECIPE_MODULES.depot_tools.gclient import (api as gclient, CONFIG_CTX as
                                                GCLIENT_CONFIG_CTX)

DEPS = [
    'chromium_checkout',
    'depot_tools/gclient',
    'recipe_engine/assertions',
    'recipe_engine/buildbucket',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/step',
    'siso',
]

PROPERTIES = {
    'ignore_input_commit': Property(kind=bool, default=False),
    'set_output_commit': Property(kind=bool, default=True),
    'no_history': Property(kind=bool, default=False),
    'shallow': Property(kind=bool, default=False),
}


@GCLIENT_CONFIG_CTX()
def revision_resolver(c):
  s = c.solutions.add()
  s.name = 'src'
  c.revisions['src-internal'] = gclient.RevisionFallbackChain('refs/heads/main')


def RunSteps(api, ignore_input_commit, set_output_commit, no_history, shallow):
  with api.assertions.assertRaisesRegexp(ValueError, 'checkout_dir is not set'):
    _ = api.chromium_checkout.checkout_dir
  with api.assertions.assertRaisesRegexp(ValueError, 'source_dir is not set'):
    _ = api.chromium_checkout.source_dir

  api.gclient.set_config(api.properties.get('gclient_config', 'chromium'))

  api.chromium_checkout.ensure_checkout(
      ignore_input_commit=ignore_input_commit,
      set_output_commit=set_output_commit,
      patch=False,
      suffix='foo',
      no_history=no_history,
      shallow=shallow)

  api.step('details', [])
  api.step.active_result.presentation.logs['details'] = [
    'affected_files: %r' % (
        api.chromium_checkout.get_files_affected_by_patch(),),
  ]

  # Checking out again is fine if the checkout_dir and source_dir are the same
  api.chromium_checkout.ensure_checkout()

def GenTests(api):
  yield api.test(
      'full_ci',
      api.platform('linux', 64),
      api.buildbucket.generic_build(),
      api.post_process(DoesNotRun, 'gerrit fetch current CL info'),
      api.post_process(StepSuccess, 'gclient config'),
      api.post_process(StepSuccess, 'bot_update'),
      api.post_process(DropExpectation),
  )

  def verify_rbe_instance(check, steps, expected):
    gclient_config = api.json.loads(steps["gclient config"].logs["config"])
    check(gclient_config["solutions"][0]["custom_vars"]["rbe_instance"] ==
          expected)
    check(gclient_config["solutions"][0]["custom_vars"]
          ["download_remoteexec_cfg"] == 'True')

  yield api.test(
      'siso_enabled',
      api.platform('linux', 64),
      api.buildbucket.generic_build(),
      api.siso.properties(project='someproj'),
      api.post_check(verify_rbe_instance,
                     'projects/someproj/instances/default_instance'),
      api.post_process(DoesNotRun, 'gerrit fetch current CL info'),
      api.post_process(StepSuccess, 'gclient config'),
      api.post_process(StepSuccess, 'bot_update'),
      api.post_process(DropExpectation),
  )

  def verify_checkout_dir(check, step_odict, expected_path):
    step = step_odict['git diff to analyze patch']
    expected_path = str(expected_path)
    check(step.cwd == expected_path)

  yield api.test(
      'win_try',
      api.buildbucket.try_build(),
      api.platform('win', 64),
      api.post_check(verify_checkout_dir,
                     api.chromium_checkout.default_checkout_dir / 'src'),
      api.post_process(StepSuccess, 'gclient config'),
      api.post_process(StepSuccess, 'gerrit fetch current CL info'),
      api.post_process(StepSuccess, 'bot_update'),
      api.post_process(StepSuccess, 'git diff to analyze patch'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'linux_try',
      api.buildbucket.try_build(),
      api.platform('linux', 64),
      api.post_check(verify_checkout_dir,
                     api.chromium_checkout.default_checkout_dir / 'src'),
      api.post_process(DoesNotRun, 'taskkill'),
      api.post_process(StepSuccess, 'gclient config'),
      api.post_process(StepSuccess, 'gerrit fetch current CL info'),
      api.post_process(StepSuccess, 'bot_update'),
      api.post_process(StepSuccess, 'git diff to analyze patch'),
      api.post_process(DropExpectation),
  )

  def verify_revision_resolver_in_log(check, steps, expected):
    gclient_config = api.json.loads(steps["gclient config"].logs["config"])
    check(gclient_config["revisions"]["src-internal"] == expected)

  yield api.test(
      'revision-resolver',
      api.properties(
          gclient_config='revision_resolver', set_output_commit=False),
      api.post_check(verify_revision_resolver_in_log,
                     "*RevisionFallbackChain*"),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'no-output-commit',
      api.buildbucket.try_build(),
      api.platform('linux', 64),
      api.properties(ignore_input_commit=True, set_output_commit=False),
      api.post_check(verify_checkout_dir,
                     api.chromium_checkout.default_checkout_dir / 'src'),
      api.post_process(DoesNotRun, 'taskkill'),
      api.post_process(StepSuccess, 'gclient config'),
      api.post_process(StepSuccess, 'gerrit fetch current CL info'),
      api.post_process(StepSuccess, 'bot_update'),
      api.post_process(StepSuccess, 'git diff to analyze patch'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'output_commit_tag',
      api.buildbucket.try_build(
          git_repo='https://chromium.googlesource.com/chromium/src.git',
          git_ref='refs/tags/100.0.0000.0',
          revision='1234567890'),
      api.platform('linux', 64),
      api.properties(set_output_commit=True),
      api.post_process(
          PropertyEquals,
          '$recipe_engine/buildbucket/output_gitiles_commit',
          {
              'host': 'chromium.googlesource.com',
              'id': '1234567890',
              'project': 'chromium/src',
              'ref': 'refs/tags/100.0.0000.0'
          },
      ),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'rdb_missing_commit_info',
      api.buildbucket.try_build(),
      api.step_data(
          'bot_update (without patch) - foo',
          api.json.output({
              'did_run': True,
              "manifest": {
                  'src': {
                      'repository':
                          'https://chromium.googlesource.com/chromium/src.git',
                      'revision':
                          ''
                  }
              },
              'patch_root': None,
              'properties': {
                  'got_revision': '',
                  'got_revision_cp': 'refs/heads/main@{#1234567890}',
              },
              'root': 'src',
              'step_text': 'text'
          })),
      api.platform('linux', 64),
      api.post_process(
          MustRun,
          'set rdb sources.missing gitiles commit info',
      ),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'no_history',
      api.platform('linux', 64),
      api.buildbucket.generic_build(),
      api.properties(no_history=True),
      api.post_process(StepSuccess, 'bot_update (without patch) - foo'),
      api.post_process(StepCommandContains, 'bot_update (without patch) - foo',
                       '--no-history'),
      api.post_process(DropExpectation),
  )

  yield api.test(
      'shallow',
      api.platform('linux', 64),
      api.buildbucket.generic_build(),
      api.properties(shallow=True),
      api.post_process(StepSuccess, 'bot_update (without patch) - foo'),
      api.post_process(StepCommandContains, 'bot_update (without patch) - foo',
                       '--shallow'),
      api.post_process(DropExpectation),
  )
