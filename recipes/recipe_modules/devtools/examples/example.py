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
    api.devtools.run_e2e(builder_config)
    with api.devtools.collect_screenshots('dummy-bucket'):
      api.step.empty('Nothing')


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


  yield (api.test('release') + try_build())

  yield (api.test('parallel release') + try_build(builder='parallel builder'))

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
