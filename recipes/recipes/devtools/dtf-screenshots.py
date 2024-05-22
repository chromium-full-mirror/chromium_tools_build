# Copyright 2023 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from contextlib import contextmanager
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from recipe_engine import post_process
from recipe_engine.recipe_api import Property


DEPS = [
    'builder_group',
    'chromium',
    'devtools',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/properties',
    'recipe_engine/step',
]


PROPERTIES = {
    'clobber':
        Property(
            kind=bool,
            help='Should the builder clean up the out/ folder before building',
            default=False),
}


def RunSteps(api, clobber):
  builder_config = 'Release'
  api.devtools.configure(
      builder_config, is_official_build=False, devtools_skip_typecheck=True)
  update_result = api.devtools.update()
  source_dir = update_result.source_root.path

  with api.devtools.depot_on_path(source_dir):
    api.devtools.clean_out_dir(source_dir, builder_config, clobber)
    api.chromium.run_gn()
    compilation_result = api.chromium.compile()
    if compilation_result.status != common_pb.SUCCESS:
      return compilation_result

    with api.devtools.collect_screenshots('devtools-frontend-screenshots'):
      update_screenshots(api, source_dir, builder_config)


def update_screenshots(api, source_dir, builder_config):
  api.devtools.rdb_node_script(
      source_dir,
      'Screenshots',
      'run_test_suite.js',
      [
          "--test-suite-path=gen/test/interactions",
          "--test-suite-source-dir=test/interactions",
          "--test-server-type='component-docs'",
          f"--target={builder_config}",
          "--mocha-fgrep='[screenshot]'",
      ],
  )


def GenTests(api):
  git_repo = 'https://chromium.googlesource.com/devtools/devtools-frontend'

  def try_build(builder, **kwargs):
    return api.buildbucket.try_build(
        project='devtools',
        builder=builder,
        git_repo=git_repo,
        change_number=91827,
        patch_set=1,
        **kwargs)

  yield api.test(
      'compile failure',
      api.builder_group.for_current('devtools-frontend'),
      try_build(builder='linux'),
      api.step_data('compile', retcode=1),
      api.post_process(post_process.DropExpectation),
      status='FAILURE',
  )

  yield api.test(
      'screeenshots',
      api.builder_group.for_current('tryserver.devtools-frontend'),
      try_build(builder='screenshots-builder'),
      status='SUCCESS',
  )
