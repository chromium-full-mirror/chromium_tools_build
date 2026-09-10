# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Recipe to test the deterministic build.

Waterfall page: https://build.chromium.org/p/chromium.swarm/waterfall

"""

from recipe_engine import post_process
from recipe_engine.engine_types import freeze
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb
from RECIPE_MODULES.build import chromium_types

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import (
  builder_group,
  chromium,
  chromium_android,
  isolate,
  siso,
)
from RECIPE_MODULES.depot_tools import bot_update, gclient
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  file,
  path,
  platform,
  properties,
  step,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  builder_group: builder_group.API
  chromium: chromium.API
  chromium_android: chromium_android.API
  context: context.API
  file: file.API
  gclient: gclient.API
  isolate: isolate.API
  path: path.API
  platform: platform.API
  properties: properties.API
  siso: siso.API
  step: step.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  chromium: chromium.TEST_API
  gclient: gclient.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  siso: siso.TEST_API
  step: step.TEST_API


# TODO(crbug.com/441328364): Migrate these configs src-side and read them in
# from input props.
DETERMINISTIC_BUILDERS = freeze(
  {
    'Mac deterministic': {
      'chromium_config': 'chromium',
      'gclient_config': 'chromium',
      'platform': 'mac',
      'targets': ['all'],
    },
    'Windows deterministic': {
      'chromium_config': 'chromium',
      'gclient_config': 'chromium',
      'platform': 'win',
      'targets': ['all'],
    },
    # TODO(b/260228493) Remove once CI backend is switched
    'Windows Cross deterministic': {
      'chromium_config': 'chromium',
      'gclient_config': 'chromium',
      'platform': 'win',
      'targets': ['all'],
      'compare_local': True,
    },
    # Debug builders
    'Mac deterministic (dbg)': {
      'chromium_config': 'chromium',
      'gclient_config': 'chromium',
      'platform': 'mac',
      'targets': ['all'],
    },
    'Deterministic Linux': {
      'chromium_config': 'chromium',
      'gclient_config': 'chromium',
      'platform': 'linux',
      'targets': ['all'],
    },
    'linux_chromium_clobber_deterministic': {
      'chromium_config': 'chromium',
      'gclient_config': 'chromium',
      'platform': 'linux',
      'targets': ['all'],
    },
    'Deterministic Linux (dbg)': {
      'chromium_config': 'chromium',
      'chromium_config_kwargs': {
        'BUILD_CONFIG': 'Debug',
      },
      'gclient_config': 'chromium',
      'platform': 'linux',
      'targets': ['all'],
      'compare_local': True,
    },
    'Deterministic Android': {
      'chromium_config': 'main_builder',
      'android_config': 'base_config',
      'gclient_config': 'chromium',
      'gclient_apply_config': ['android'],
      'chromium_config_kwargs': {
        'BUILD_CONFIG': 'Release',
        'TARGET_BITS': 32,
        'TARGET_PLATFORM': 'android',
        'TARGET_ARCH': 'arm',
      },
      'platform': 'linux',
      'targets': ['all'],
    },
    'Deterministic Android (dbg)': {
      'chromium_config': 'main_builder',
      'android_config': 'base_config',
      'gclient_config': 'chromium',
      'gclient_apply_config': ['android'],
      'chromium_config_kwargs': {
        'BUILD_CONFIG': 'Debug',
        'TARGET_BITS': 32,
        'TARGET_PLATFORM': 'android',
        'TARGET_ARCH': 'arm',
      },
      'platform': 'linux',
      'targets': ['all'],
    },
    'Deterministic Fuchsia (dbg)': {
      'chromium_config': 'chromium',
      'chromium_config_kwargs': {
        'BUILD_CONFIG': 'Debug',
        'TARGET_BITS': 64,
        'TARGET_PLATFORM': 'fuchsia',
      },
      'gclient_config': 'chromium',
      'gclient_apply_config': ['fuchsia_x64'],
      'platform': 'linux',
      'targets': ['all'],
    },
    'android-desktop-arm64-deterministic-rel': {
      'chromium_config': 'main_builder',
      'android_config': 'base_config',
      'gclient_config': 'chromium',
      'gclient_apply_config': ['android'],
      'chromium_config_kwargs': {
        'BUILD_CONFIG': 'Release',
        'TARGET_BITS': 64,
        'TARGET_PLATFORM': 'android',
        'TARGET_ARCH': 'arm',
      },
      'platform': 'linux',
      'targets': ['all'],
    },
    'android-desktop-arm64-deterministic-dbg': {
      'chromium_config': 'main_builder',
      'android_config': 'base_config',
      'gclient_config': 'chromium',
      'gclient_apply_config': ['android'],
      'chromium_config_kwargs': {
        'BUILD_CONFIG': 'Debug',
        'TARGET_BITS': 64,
        'TARGET_PLATFORM': 'android',
        'TARGET_ARCH': 'arm',
      },
      'platform': 'linux',
      'targets': ['all'],
    },
    'android-desktop-x64-deterministic-rel': {
      'chromium_config': 'main_builder',
      'android_config': 'base_config',
      'gclient_config': 'chromium',
      'gclient_apply_config': ['android'],
      'chromium_config_kwargs': {
        'BUILD_CONFIG': 'Release',
        'TARGET_BITS': 64,
        'TARGET_PLATFORM': 'android',
        'TARGET_ARCH': 'intel',
      },
      'platform': 'linux',
      'targets': ['all'],
    },
    'android-desktop-x64-deterministic-dbg': {
      'chromium_config': 'main_builder',
      'android_config': 'base_config',
      'gclient_config': 'chromium',
      'gclient_apply_config': ['android'],
      'chromium_config_kwargs': {
        'BUILD_CONFIG': 'Debug',
        'TARGET_BITS': 64,
        'TARGET_PLATFORM': 'android',
        'TARGET_ARCH': 'intel',
      },
      'platform': 'linux',
      'targets': ['all'],
    },
  }
)

# Trybots to mirror the actions of builders
DETERMINISTIC_TRYBOTS = freeze(
  {
    'android-deterministic-rel': 'Deterministic Android',
    'android-deterministic-dbg': 'Deterministic Android (dbg)',
    'fuchsia-deterministic-dbg': 'Deterministic Fuchsia (dbg)',
    'android-desktop-arm64-deterministic-rel': 'android-desktop-arm64-deterministic-rel',
    'android-desktop-arm64-deterministic-dbg': 'android-desktop-arm64-deterministic-dbg',
    'android-desktop-x64-deterministic-rel': 'android-desktop-x64-deterministic-rel',
    'android-desktop-x64-deterministic-dbg': 'android-desktop-x64-deterministic-dbg',
  }
)


def MoveBuildDirectory(api: DEPS, src_dir, dst_dir):
  cmd = ['python3', api.resource('move.py'), src_dir, dst_dir]
  api.step('Move %s to %s' % (src_dir, dst_dir), cmd)


def ConfigureChromiumBuilder(api: DEPS, recipe_config):
  api.chromium.set_config(
    recipe_config['chromium_config'],
    **recipe_config.get('chromium_config_kwargs', {'BUILD_CONFIG': 'Release'}),
  )
  api.gclient.set_config(
    recipe_config['gclient_config'],
    **recipe_config.get('gclient_config_kwargs', {}),
  )

  for c in recipe_config.get('gclient_apply_config', []):
    api.gclient.apply_config(c)

  if recipe_config.get('android_config'):
    api.chromium_android.set_config(recipe_config.get('android_config'))

  api.siso.enable_download_remoteexec_cfg_hook()

  # Checkout chromium.
  return api.bot_update.ensure_checkout()


def RunSteps(api: DEPS):
  buildername = api.buildbucket.builder_name
  if buildername in DETERMINISTIC_BUILDERS:
    recipe_config = DETERMINISTIC_BUILDERS[buildername]
  else:
    recipe_config = DETERMINISTIC_BUILDERS[DETERMINISTIC_TRYBOTS[buildername]]

  # Set up a named cache so runhooks doesn't redownload everything on each run.
  solution_path = api.path.cache_dir / 'builder'
  api.file.ensure_directory('init cache if not exists', solution_path)

  with api.context(cwd=solution_path):
    update_result = ConfigureChromiumBuilder(api, recipe_config)
  source_dir = update_result.source_root.path
  default_build_dir = api.chromium.default_build_dir(source_dir)

  # The default setup by this recipe is to do a clobber build in one directory,
  # move it elsewhere, then do another clobber build in the original directory,
  # and then to compare the two directories. This doesn't check that
  # different build directories produce the same output, and it doesn't check
  # that incremental builds produce the same output as clobber builds.
  # If check_different_build_dirs is set, the recipe instead does an incremental
  # build in the usual build dir and a clobber build in a differently-named
  # build dir and then compares the outputs.
  # TODO(thakis): Do this on all platforms, https://crbug.com/899438
  check_different_build_dirs = api.chromium.c.TARGET_PLATFORM in [
    'fuchsia',
    'linux',
    'win',
  ]

  # Since disk lacks in Mac, we need to remove files before build.
  # In check_different_build_dirs, only the .2 build dir exists here.
  for ext in '12':
    p = str(default_build_dir).rstrip('\\/') + '.' + ext
    api.file.rmtree('rmtree %s' % p, p)
  if check_different_build_dirs:
    # In this setup, one build dir does incremental builds. Make sure no stale
    # .runtime_deps (explicitly also in subdirectories) files hang around.
    api.file.rmglob(
      'rm old .runtime_deps', default_build_dir, '**/*.runtime_deps'
    )

  targets = recipe_config['targets']

  with api.context(cwd=solution_path):
    api.chromium.runhooks(source_dir, default_build_dir)

  # Temp directories to store the two builds.
  first_dir = default_build_dir
  if not check_different_build_dirs:
    first_dir = default_build_dir.parent / (
      str(default_build_dir.name).rstrip('\\/') + '.1'
    )
  second_dir = default_build_dir.parent / (
    str(default_build_dir.name).rstrip('\\/') + '.2'
  )

  # Whether do first build in local or use remoteexec.
  compare_local = recipe_config.get('compare_local', False)

  remote_phase = 'remoteexec'

  # Do a first build and move the build artifact to the temp directory.
  builder_id = chromium_types.BuilderId.create_for_group(
    api.builder_group.for_current, buildername
  )
  api.chromium.mb_gen(
    source_dir,
    default_build_dir,
    builder_id,
    phase='local' if compare_local else None,
  )
  api.chromium.mb_isolate_everything(
    source_dir,
    default_build_dir,
    builder_id,
    phase='local' if compare_local else None,
  )

  raw_result = api.chromium.compile(
    source_dir, default_build_dir, targets=targets, name='First build'
  )
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  if not check_different_build_dirs:
    MoveBuildDirectory(api, str(default_build_dir), str(first_dir))

  # Do the second build and move the build artifact to the temp directory.
  build_dir = default_build_dir
  if check_different_build_dirs:
    build_dir = second_dir

  api.chromium.mb_gen(
    source_dir,
    build_dir,
    builder_id,
    phase=remote_phase if compare_local else None,
  )

  api.chromium.mb_isolate_everything(
    source_dir,
    build_dir,
    builder_id,
    phase=remote_phase if compare_local else None,
  )
  raw_result = api.chromium.compile(
    source_dir, build_dir, targets=targets, name='Second build'
  )
  if raw_result.status != common_pb.SUCCESS:
    return raw_result

  if not check_different_build_dirs:
    MoveBuildDirectory(api, str(default_build_dir), str(second_dir))

  # Compare the artifacts from the 2 builds, raise an exception if they're
  # not equals.
  # TODO(sebmarchand): Do a smarter comparison.
  try:
    api.isolate.compare_build_artifacts(
      source_dir, str(first_dir), str(second_dir)
    )
  finally:
    # remove Debug* to free up disk space in builder cache.
    api.file.rmtree('rmtree %s' % first_dir, first_dir)
    api.file.rmtree('rmtree %s' % second_dir, second_dir)


def _sanitize_nonalpha(text):
  return ''.join(c if c.isalnum() else '_' for c in text)


def GenTests(api: TEST_DEPS):
  builder_group = 'chromium.swarm'
  for buildername in DETERMINISTIC_BUILDERS:
    test_name = 'full_ci_%s_%s' % (
      _sanitize_nonalpha(builder_group),
      _sanitize_nonalpha(buildername),
    )
    yield api.test(
      test_name,
      api.chromium.ci_build(builder_group=builder_group, builder=buildername),
      api.platform(DETERMINISTIC_BUILDERS[buildername]['platform'], 64),
      api.properties(
        buildername=buildername, buildnumber=571, configuration='Release'
      ),
      api.post_process(post_process.DropExpectation),
    )
    yield api.test(
      test_name + '_fail',
      api.chromium.ci_build(builder_group=builder_group, builder=buildername),
      api.platform(DETERMINISTIC_BUILDERS[buildername]['platform'], 64),
      api.properties(
        buildername=buildername, buildnumber=571, configuration='Release'
      ),
      api.step_data('compare_build_artifacts', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
    )

  for trybotname in DETERMINISTIC_TRYBOTS:
    test_name = 'full_try_%s_%s' % (
      _sanitize_nonalpha(builder_group),
      _sanitize_nonalpha(trybotname),
    )
    yield api.test(
      test_name,
      api.chromium.try_build(builder_group=builder_group, builder=trybotname),
      api.properties(buildername=trybotname, buildnumber=571),
      api.platform(
        DETERMINISTIC_BUILDERS[DETERMINISTIC_TRYBOTS[trybotname]]['platform'],
        64,
      ),
      api.properties(configuration='Release'),
      api.post_process(post_process.DropExpectation),
    )
    yield api.test(
      test_name + '_fail',
      api.chromium.try_build(builder_group=builder_group, builder=trybotname),
      api.properties(buildername=trybotname, buildnumber=571),
      api.platform(
        DETERMINISTIC_BUILDERS[DETERMINISTIC_TRYBOTS[trybotname]]['platform'],
        64,
      ),
      api.properties(configuration='Release'),
      api.step_data('compare_build_artifacts', retcode=1),
      api.expect_status('FAILURE'),
      api.post_process(post_process.DropExpectation),
    )

  yield api.test(
    'first_build_compile_fail',
    api.chromium.ci_build(
      builder_group=builder_group, builder='android-deterministic-dbg'
    ),
    api.properties(buildername='android-deterministic-dbg', buildnumber=571),
    api.platform(
      DETERMINISTIC_BUILDERS['Deterministic Android (dbg)']['platform'], 64
    ),
    api.properties(configuration='Release'),
    api.step_data('First build', retcode=1),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'second_build_compile_fail',
    api.chromium.ci_build(
      builder_group=builder_group, builder='android-deterministic-dbg'
    ),
    api.properties(buildername='android-deterministic-dbg', buildnumber=571),
    api.platform(
      DETERMINISTIC_BUILDERS['Deterministic Android (dbg)']['platform'], 64
    ),
    api.properties(configuration='Release'),
    api.step_data('Second build', retcode=1),
    api.expect_status('FAILURE'),
    api.post_process(post_process.DropExpectation),
  )
