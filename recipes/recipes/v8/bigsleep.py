# Copyright 2026 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Recipe for V8 bigsleep uploads.
"""

from recipe_engine.post_process import DropExpectation, MustRun

DEPS = [
    'chromium',
    'recipe_engine/path',
    'recipe_engine/step',
    'v8',
]


def RunSteps(api):
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


def GenTests(api):
  yield api.test(
      'basic',
      api.chromium.ci_build(
          builder_group='client.v8',
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder='v8_bigsleep',
      ),
      api.v8.check_in_any_arg('gn', 'is_debug=true'),
      api.v8.check_in_any_arg('gn', 'target_cpu="x64"'),
      api.v8.check_in_any_arg('gn', '--export-compile-commands'),
      api.post_process(MustRun, 'gn', 'compile', 'create snapshot'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )
