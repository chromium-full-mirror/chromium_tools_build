# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.recipe_api import Property

DEPS = [
    'devtools',
    'recipe_engine/buildbucket',
    'recipe_engine/properties',
    'recipe_engine/resultdb',
    'recipe_engine/step',
]

PROPERTIES = {
    'builder_config': Property(kind=str, default='Release'),
    'clobber': Property(kind=bool, default=False),
}


def RunSteps(api, builder_config, clobber):

  api.devtools.configure(
      builder_config, is_official_build=True, devtools_skip_typecheck=True)
  api.devtools.update()

  with api.devtools.depot_on_path():
    api.devtools.clean_out_dir(builder_config, clobber)

    if api.devtools.is_parallel_run():
      api.devtools.run_e2e(builder_config, run_mode='parallel')
      api.devtools.run_e2e(builder_config, run_mode='sequential')
    else:
      api.devtools.run_e2e(builder_config)

    with api.devtools.collect_screenshots_on_trybot('dummy-bucket'):
      api.step('Nothing', [])


def GenTests(api):
  git_repo = 'https://chromium.googlesource.com/devtools/devtools-frontend'

  def try_build(builder='builder', **kwargs):
    return api.buildbucket.try_build(
        project='devtools',
        builder=builder,
        git_repo=git_repo,
        change_number=91827,
        patch_set=1,
        **kwargs)

  def ci_build(builder='Builder'):
    return api.buildbucket.ci_build(
        project='devtools', builder=builder, git_repo=git_repo)

  yield (api.test('release') + try_build())

  yield (api.test('ci_release') +
         ci_build()+
         api.post_process(post_process.DoesNotRun, 'upload screenshots') +
         api.post_process(post_process.StatusSuccess) +
         api.post_process(post_process.DropExpectation))

  yield (api.test('parallel release') + try_build(builder='parallel builder') +
         api.post_process(post_process.MustRun, 'E2E tests (Parallel)') +
         api.post_process(post_process.MustRun, 'E2E tests (Sequential)') +
         api.post_process(
             post_process.StepCommandContains, 'E2E tests (Parallel)', [
                 '--jobs=4',
             ]) + api.post_process(post_process.StepCommandDoesNotContain,
                                   'E2E tests (Parallel)', [
                                       '--mocha-fgrep=[sequential]',
                                   ]) +
         api.post_process(
             post_process.StepCommandContains, 'E2E tests (Sequential)', [
                 '--mocha-fgrep=[sequential]',
             ]) + api.post_process(post_process.StepCommandDoesNotContain,
                                   'E2E tests (Sequential)', [
                                       '--jobs=4',
                                   ]) +
         api.post_process(post_process.StatusSuccess) +
         api.post_process(post_process.DropExpectation))

  yield (api.test('debug') + api.properties(builder_config='Debug') +
         try_build() +
         api.post_process(post_process.MustRun, 'clean outdir') +
         api.post_process(post_process.StatusSuccess) +
         api.post_process(post_process.DropExpectation))

  yield (api.test('clobber') + api.properties(clobber=True) +
         try_build() +
         api.post_process(post_process.MustRun, 'clean outdir') +
         api.post_process(post_process.StatusSuccess) +
         api.post_process(post_process.DropExpectation))
