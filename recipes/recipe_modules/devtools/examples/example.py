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


def GenTests(api):
  git_repo = 'https://chromium.googlesource.com/devtools/devtools-frontend'
  yield (api.test('release') +
         api.buildbucket.ci_build(project='devtools', git_repo=git_repo))

  yield (api.test('debug') + api.properties(builder_config='Debug') +
         api.buildbucket.ci_build(project='devtools', git_repo=git_repo) +
         api.post_process(post_process.MustRun, 'clean outdir') +
         api.post_process(post_process.StatusSuccess) +
         api.post_process(post_process.DropExpectation))

  yield (api.test('clobber') + api.properties(clobber=True) +
         api.buildbucket.ci_build(project='devtools', git_repo=git_repo) +
         api.post_process(post_process.MustRun, 'clean outdir') +
         api.post_process(post_process.StatusSuccess) +
         api.post_process(post_process.DropExpectation))
