# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Recipe for V8 bigsleep uploads.
"""

from recipe_engine.post_process import DropExpectation, MustRun

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium, v8
from RECIPE_MODULES.depot_tools import bot_update, gsutil, tryserver
from RECIPE_MODULES.recipe_engine import (
    commit_position,
    path,
    step,
    time,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  chromium: chromium.API
  commit_position: commit_position.API
  gsutil: gsutil.API
  path: path.API
  step: step.API
  time: time.API
  tryserver: tryserver.API
  v8: v8.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  v8: v8.TEST_API


def RunSteps(api: DEPS):
  bot_config = {
      'chromium_apply_config': ['default_compiler', 'gn'],
      'v8_config_kwargs': {
          'BUILD_CONFIG': 'Debug',
          'TARGET_ARCH': 'intel',
          'TARGET_BITS': 64,
          'TARGET_PLATFORM': 'linux',
      },
  }
  api.v8.apply_bot_config(bot_config)

  gn_args = [
      'is_asan=true',
      'is_component_build=false',
      'symbol_level=2',
      'target_os="linux"',
      'use_remoteexec=true',
      'use_siso=true',
      'v8_enable_backtrace=true',
      'v8_enable_fast_mksnapshot=true',
      'v8_enable_memory_corruption_api=true',
      'v8_enable_slow_dchecks=false',
      'v8_optimized_debug=false',
  ]
  api.chromium.c.gn_args.extend(gn_args)

  update_result = api.v8.checkout()
  source_dir = update_result.source_root.path
  build_dir = api.v8.build_dir(source_dir)
  api.v8.runhooks(source_dir, build_dir)
  api.chromium.run_gn(
      source_dir, build_dir, extra_args=['--export-compile-commands'])
  api.chromium.compile(source_dir, build_dir)

  snapshot_file = api.path.cleanup_dir.joinpath('snapshot.tgz')
  api.v8.vpython(
      'create snapshot',
      source_dir / 'tools' / 'bigsleep' / 'create_snapshot.py',
      args=[
          '--v8_dir',
          source_dir,
          '--build_dir',
          build_dir,
          '--output_file',
          snapshot_file,
      ],
  )

  gs_bucket = 'chromium-v8'
  if api.tryserver.is_tryserver:
    gs_dest = 'try/linux/x64/debug'
    gs_filename = 'v8-linux-x64-debug-%d-%d-%d.tgz' % (
        api.tryserver.gerrit_change_number,
        api.tryserver.gerrit_patchset_number,
        int(api.time.time()),
    )
    gs_path = f'bigsleep/{gs_dest}/{gs_filename}'
    api.step.empty(
        'dry run: gsutil upload',
        step_text=f'Would have uploaded {snapshot_file} to gs://{gs_bucket}/{gs_path}',
    )
  else:
    gs_dest = 'linux/x64/debug'
    revision_number = api.v8.revision_number
    assert revision_number, "Commit position number is required."
    gs_filename = f'v8-linux-x64-debug-{revision_number}.tgz'
    gs_path = f'bigsleep/{gs_dest}/{gs_filename}'

    api.gsutil.upload(
        snapshot_file,
        gs_bucket,
        gs_path,
        args=['-a', 'public-read'],
    )

    api.gsutil.copy(
        gs_bucket,
        gs_path,
        gs_bucket,
        f'bigsleep/{gs_dest}/latest.tgz',
        args=['-a', 'public-read'],
    )


def GenTests(api: TEST_DEPS):
  yield (
      api.v8.test('client.v8', 'v8_bigsleep') + api.step_data(
          'bot_update',
          api.bot_update.output_json(
              first_sln='v8',
              got_revision_mapping={'got_revision': 'v8'},
              commit_positions=True,
          )) +
      api.v8.check_in_any_arg('gsutil upload', 'bigsleep/linux/x64/debug') +
      api.v8.check_in_any_arg('gsutil upload', 'v8-linux-x64-debug-50110.tgz') +
      api.v8.check_in_any_arg('gsutil copy',
                              'bigsleep/linux/x64/debug/latest.tgz') +
      api.v8.check_in_any_arg('gn', 'is_asan=true') +
      api.v8.check_in_any_arg('gn', 'is_debug=true') +
      api.v8.check_in_any_arg('gn', 'target_cpu="x64"') +
      api.v8.check_in_any_arg('gn', '--export-compile-commands') +
      api.post_process(MustRun, 'gn', 'compile', 'create snapshot') +
      api.post_process(DropExpectation))

  yield (api.v8.test('tryserver.v8', 'v8_bigsleep_try', suffix='tryserver') +
         api.step_data(
             'bot_update',
             api.bot_update.output_json(
                 first_sln='v8',
                 got_revision_mapping={'got_revision': 'v8'},
                 commit_positions=True,
             )) + api.post_process(MustRun, 'dry run: gsutil upload') +
         api.post_process(DropExpectation))

  yield (api.v8.test('client.v8', 'v8_bigsleep', suffix='no_commit_position') +
         api.step_data(
             'bot_update',
             api.bot_update.output_json(
                 first_sln='v8',
                 got_revision_mapping={'got_revision': 'v8'},
                 commit_positions=False,
             )) + api.expect_exception('AssertionError') +
         api.post_process(DropExpectation))
