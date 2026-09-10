# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.config import Single
from recipe_engine.engine_types import freeze
from recipe_engine.recipe_api import Property

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium_checkout
from RECIPE_MODULES.depot_tools import (
  bot_update,
  gclient,
  git,
  gitiles,
  gsutil,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  file,
  path,
  properties,
  raw_io,
  runtime,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium_checkout: chromium_checkout.API
  context: context.API
  file: file.API
  gclient: gclient.API
  git: git.API
  gitiles: gitiles.API
  gsutil: gsutil.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  runtime: runtime.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  gitiles: gitiles.TEST_API
  properties: properties.TEST_API
  runtime: runtime.TEST_API


PROPERTIES = {
  'project': Property(
    kind=str,
    default=None,
    help='Project for which LKGR should be calculated.',
  ),
  'repo': Property(
    kind=str, default=None, help='Repo for which LKGR should be updated.'
  ),
  'ref': Property(kind=str, default=None, help='LKGR ref to update.'),
  'src_ref': Property(
    kind=str, default='refs/heads/main', help='Source repo ref to fetch from.'
  ),
  'config': Property(
    kind=dict,
    default=None,
    help='Config dict to use. See infra.services.lkgr_finder for more.',
  ),
  'lkgr_status_gs_path': Property(
    kind=str,
    default=None,
    help=('Google storage path to which LKGR status reports will be uploaded.'),
  ),
  'allowed_lag': Property(
    kind=Single((int, float)),
    default=None,
    help='Hours before an LKGR is considered out of date.',
  ),
}


def RunSteps(
  api: DEPS,
  project,
  repo,
  ref,
  config,
  lkgr_status_gs_path,
  allowed_lag,
  src_ref,
):
  if not project or not repo or not ref:
    api.step.empty(
      'configuration missing',
      status=api.step.FAILURE,
      step_text=(
        'lkgr_finder requires `project`, `repo`, and `ref` '
        'properties to be set.'
      ),
    )

  api.gclient.set_config('infra_superproject')

  # Projects can define revision mappings that conflict with infra revision
  # mapping, so we overide them here to only map infra's revision so that it
  # shows up on the buildbot page.
  api.gclient.c.got_revision_mapping = {}
  api.gclient.c.got_revision_reverse_mapping = {'got_revision': 'infra'}

  checkout_dir = api.chromium_checkout.default_checkout_dir
  with api.context(cwd=checkout_dir):
    api.bot_update.ensure_checkout()

  current_lkgr = api.gitiles.commit_log(
    repo, ref, step_name='read lkgr from ref'
  )['commit']

  api.file.ensure_directory('mkdirs builder/lw', checkout_dir / 'lw')
  args = [
    '-vpython-spec',
    '.vpython3',
    '-m',
    'infra.services.lkgr_finder',
    '--project=%s' % project,
    '--verbose',
    '--read-from-file',
    api.raw_io.input_text(current_lkgr),
    '--write-to-file',
    api.raw_io.output_text(name='lkgr_hash'),
    '--workdir',
    checkout_dir / 'lw',
  ]
  if not api.runtime.is_experimental:
    args.append('--email-errors')
  if config:
    args.extend(
      [
        '--project-config-file',
        api.raw_io.input_text(repr(config), name='config_pyl'),
      ]
    )
  step_test_data = api.raw_io.test_api.output_text(
    'deadbeef' * 5, name='lkgr_hash'
  )

  if allowed_lag is not None:
    args.append('--allowed-lag=%d' % allowed_lag)

  if lkgr_status_gs_path:
    args += ['--html', api.raw_io.output_text(name='html')]
    step_test_data += api.raw_io.test_api.output_text(
      '<html>lkgr</html>', name='html'
    )

  try:
    with api.context(cwd=checkout_dir / 'infra'):
      api.step(
        'calculate %s lkgr' % project,
        ['vpython3'] + args,
        step_test_data=lambda: step_test_data,
      )
  finally:
    step_result = api.step.active_result
    html_status = None
    if (
      hasattr(step_result, 'raw_io')
      and hasattr(step_result.raw_io, 'output_texts')
      and hasattr(step_result.raw_io.output_texts, 'get')
    ):
      html_status = step_result.raw_io.output_texts.get('html')
    if lkgr_status_gs_path and html_status:
      if api.runtime.is_experimental:
        api.step('fake HTML status upload', cmd=None)
      else:
        api.gsutil.upload(
          api.raw_io.input_text(html_status),
          lkgr_status_gs_path,
          '%s-lkgr-status.html' % project,
          args=['-a', 'public-read'],
          metadata={'Content-Type': 'text/html'},
          link_name='%s-lkgr-status.html' % project,
        )

  # We check out regularly, not only on lkgr update, to catch infra failures
  # on check-out early.
  # TODO(machenbach,tandrii): re-use checkout that the lkgr_finder tool has
  # already made inside its own workdir. Furthermore, one can execute git push
  # command even without having full checkout.
  api.git.checkout(
    url=repo,
    dir_path=checkout_dir / 'workdir',
    submodules=False,
    submodule_update_recursive=False,
    # For some reason, git cache doesn't make this faster crbug.com/860112.
    use_git_cache=True,
    ref=src_ref,
  )

  new_lkgr = step_result.raw_io.output_texts['lkgr_hash']
  if new_lkgr and new_lkgr != current_lkgr:
    with api.context(cwd=checkout_dir / 'workdir'):
      if api.runtime.is_experimental:
        api.step('fake lkgr push', cmd=None)
      else:
        api.git(
          'push', repo, '%s:%s' % (new_lkgr, ref), name='push lkgr to ref'
        )


def GenTests(api: TEST_DEPS):

  def test_build(buildername):
    return api.buildbucket.generic_build(builder=buildername)

  def test_build_and_data(buildername):
    return test_build(buildername) + api.step_data(
      'read lkgr from ref',
      api.gitiles.make_commit_test_data('deadbeef1', 'Commit1'),
    )

  def test_props(**additional_props):
    return api.properties(
      project='custom',
      repo='https://custom.googlesource.com/src',
      ref='refs/heads/lkgr',
      lkgr_status_gs_path='custom/lkgr-status',
      **additional_props,
    )

  yield api.test(
    'v8_experimental',
    test_build_and_data('V8 lkgr finder'),
    test_props(),
    api.runtime(is_experimental=True),
  )

  for retcode, suffix, status in [
    (0, '', 'SUCCESS'),
    (1, '_failure', 'FAILURE'),
    (2, '_stale', 'FAILURE'),
  ]:
    yield api.test(
      'custom_properties' + suffix,
      test_build_and_data('custom-lkgr-finder'),
      api.step_data('calculate custom lkgr', retcode=retcode),
      test_props(),
      api.post_process(post_process.MustRun, 'calculate custom lkgr'),
      api.expect_status(status),
    )

  yield api.test(
    'missing_all_properties',
    test_build('missing-lkgr-finder'),
    api.post_process(post_process.MustRun, 'configuration missing'),
    api.post_process(post_process.DropExpectation),
    api.expect_status('FAILURE'),
  )

  yield api.test(
    'allowed_lag',
    test_build_and_data('allowed_lag'),
    test_props(allowed_lag=4),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'custom_config',
    test_build_and_data('custom-configuration'),
    test_props(
      src_ref='refs/heads/main',
      config={
        'project': 'custom',
        'source_url': 'https://custom.googlesource.com/src',
        'masters': {
          'custom.foo': {
            'builders': [
              'custom-foo-builder',
            ],
          },
        },
      },
    ),
    api.post_process(post_process.MustRun, 'calculate custom lkgr'),
    api.post_process(
      post_process.StepCommandContains,
      'calculate custom lkgr',
      ['--project-config-file'],
    ),
    api.post_process(post_process.DropExpectation),
  )
