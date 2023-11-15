# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.recipes.build.art import InputProperties

DEPS = [
  'recipe_engine/buildbucket',
  'recipe_engine/context',
  'recipe_engine/cipd',
  'recipe_engine/defer',
  'recipe_engine/file',
  'recipe_engine/path',
  'recipe_engine/properties',
  'recipe_engine/runtime',
  'recipe_engine/step',
  'recipe_engine/url',
  'repo',
]


# Value passed to option `-j` of command `repo sync`.
REPO_SYNC_JOBS = 16

PROPERTIES = InputProperties

HOST_TEST_INTERPRETER_MAKE_JOBS = 5

_TARGET_DEVICE_MAP = {
    'walleye-armv7': {
        'bitness': 32,
        'product': 'arm_krait',
    },
    'walleye-armv8': {
        'bitness': 64,
        'product': 'armv8',
    },
    'angler-armv7': {
        'bitness': 32,
        'product': 'arm_krait',
    },
    'fugu': {
        'bitness': 32,
        'product': 'silvermont',
    },
    'angler-armv8': {
        'bitness': 64,
        'product': 'armv8',
    },
    'bullhead-armv8': {
        'bitness': 64,
        'product': 'armv8',
    },
    'bullhead-armv7': {
        'bitness': 32,
        'product': 'arm_krait',
    },
    'qemu-riscv64': {
        'bitness': 64,
        'product': 'riscv64',
    },
}

def RunSteps(api, props):
  manifest_branch = props.manifest_branch or 'master-art'
  if props.device:
    # Use different cache directory for RISCV to avoid interference.
    cache_name = "builder" if props.device == 'qemu-riscv64' else "art"
    with api.context(cwd=api.path['cache'].join(cache_name)):
      setup_target(
        api,
        device=props.device,
        debug=props.debug,
        build_only=props.build_only,
        concurrent_collector=props.concurrent_collector,
        generational_cc=props.generational_cc,
        heap_poisoning=props.heap_poisoning,
        gcstress=props.gcstress,
        on_virtual_machine=props.on_virtual_machine,
        manifest_branch=manifest_branch or 'master-art'
      )
  else:
    with api.context(cwd=api.path['cache'].join('art')):
      setup_host_x86(
        api,
        debug=props.debug,
        bitness=props.bitness,
        concurrent_collector=props.concurrent_collector,
        generational_cc=props.generational_cc,
        heap_poisoning=props.heap_poisoning,
        gcstress=props.gcstress,
        cdex_level=props.cdex_level or 'none',
        manifest_branch=manifest_branch or 'master-art'
      )

def checkout(api, manifest_branch):
  # (https://crbug.com/1153114): do not attempt to update repo when
  # 'repo sync' runs.
  env = {'DEPOT_TOOLS_UPDATE': '0'}
  with api.context(env=env):
    api.repo.init('https://android.googlesource.com/platform/manifest', '-b',
                  manifest_branch)
    api.repo.sync('-c', '-j%d' % (REPO_SYNC_JOBS), "--no-tags")

    build_input = api.buildbucket.build.input
    if build_input.gerrit_changes:
      for change in build_input.gerrit_changes:
        api.repo(
          ['download', change.project, f"{change.change}/{change.patchset}"],
          f"checkout change ref: {change.change}/{change.patchset}",
        )

    # TODO(b/283282132): Add codepath for checking out revision with
    # build_input.gitiles_commit

    api.repo.manifest()


def clobber(api):
  # buildbot sets 'clobber' to the empty string which is falsey, check with 'in'
  if 'clobber' in api.properties:
    api.file.rmtree('clobber', api.context.cwd.join('out'))

# Calls api.cipd.ensure_tool, then modifies the path returned by the call to
# join the subdir.
def ensure_tool(api, package, version, subdir=""):
  dirname = api.path.split(api.cipd.ensure_tool(
    package=package,
    version=version,
  ))[0]
  return api.path.abs_to_path(api.path.abspath(dirname.join(subdir)))

def setup_host_x86(api,
                   debug,
                   bitness,
                   concurrent_collector=True,
                   generational_cc=True,
                   heap_poisoning=False,
                   gcstress=False,
                   cdex_level='none',
                   manifest_branch="master-art"):
  checkout(api, manifest_branch)
  clobber(api)

  build_top_dir = api.context.cwd
  art_tools = api.context.cwd.join('art', 'tools')
  # For host, the TARGET_PRODUCT isn't relevant.
  env = {
      'TARGET_PRODUCT':
          'armv8',
      'TARGET_BUILD_VARIANT':
          'eng',
      'TARGET_BUILD_TYPE':
          'release',
      'TARGET_RELEASE':
          'trunk_staging',
      'LANG':
          'en_US.UTF-8',
      'SOONG_ALLOW_MISSING_DEPENDENCIES':
          'true',
      'BUILD_BROKEN_DISABLE_BAZEL':
          'true',
      'TARGET_BUILD_UNBUNDLED':
          'true',
      'ANDROID_BUILD_TOP':
          build_top_dir,
      'PATH':
          str(build_top_dir.join('out', 'host', 'linux-x86', 'bin')) +
          api.path.pathsep + str(
              build_top_dir.join('prebuilts', 'jdk', 'jdk17', 'linux-x86',
                                 'bin')) + api.path.pathsep + '%(PATH)s',
      'ART_TEST_RUN_TEST_2ND_ARCH':
          'false',
      'ART_TEST_KEEP_GOING':
          'true'
  }

  if bitness == 32:
    env.update({ 'HOST_PREFER_32_BIT' : 'true' })

  if concurrent_collector:
    env.update({ 'ART_USE_READ_BARRIER' : 'true' })
  else:
    env.update({ 'ART_USE_READ_BARRIER' : 'false' })

  # Note: Generational CC only makes sense when read barriers are used
  # (i.e. when the Concurrent Copying collector is used).
  if generational_cc:
    env.update({ 'ART_USE_GENERATIONAL_CC' : 'true' })
  else:
    env.update({ 'ART_USE_GENERATIONAL_CC' : 'false' })

  if heap_poisoning:
    env.update({ 'ART_HEAP_POISONING' : 'true' })
  else:
    env.update({ 'ART_HEAP_POISONING' : 'false' })

  env.update({ 'ART_DEFAULT_COMPACT_DEX_LEVEL' : cdex_level })

  # Common options passed to testrunner.py.
  testrunner_cmd = [
      './art/test/testrunner/testrunner.py', '--verbose', '--host'
  ]

  if debug:
    testrunner_cmd += ['--debug']
  else:
    testrunner_cmd += ['--ndebug']

  if gcstress:
    testrunner_cmd += ['--gcstress']

  # Pass down the cdex option to testrunner.py since it doesn't use the build
  # default.
  if cdex_level != 'none':
    testrunner_cmd += ['--cdex-' + cdex_level]

  with api.context(env=env):
    api.step('build',
             [art_tools.join('buildbot-build.sh'), '--host', '--installclean'])

    with api.defer.context(collect_step_name=None) as defer:
      defer(api.step, 'test gtest', [
          'build/soong/soong_ui.bash', '--make-mode',
          'test-art-host-gtest%d' % bitness
      ])

      defer(api.step, 'test optimizing', testrunner_cmd + ['--optimizing'])

      defer(api.step, 'test debuggable',
            testrunner_cmd + ['--jit', '--debuggable'])

      # Use a lower `-j` number for interpreter, some tests take a long time
      # to run on it.
      defer(
          api.step,
          'test interpreter', testrunner_cmd +
          ['-j%d' % (HOST_TEST_INTERPRETER_MAKE_JOBS), '--interpreter'])

      defer(api.step, 'test baseline', testrunner_cmd + ['--baseline'])

      defer(api.step, 'test jit', testrunner_cmd + ['--jit'])

      if cdex_level != "none":
        defer(
            api.step,
            'test cdex-redefine-stress-optimizing', testrunner_cmd +
            ['--optimizing', '--redefine-stress', '--debuggable'])
        defer(
            api.step,
            'test cdex-redefine-stress-jit',
            testrunner_cmd + ['--jit', '--redefine-stress', '--debuggable'])

      defer(api.step, 'test speed-profile',
            testrunner_cmd + ['--speed-profile'])

      libcore_command = [art_tools.join('run-libcore-tests.sh'),
                         '--mode=host',
                         '--variant=X%d' % bitness]
      if debug:
        libcore_command.append('--debug')
      if gcstress:
        libcore_command += ['--gcstress']

      defer(api.step, 'test libcore', libcore_command)

      libjdwp_run = art_tools.join('run-libjdwp-tests.sh')
      libjdwp_common_command = [libjdwp_run,
                                '--mode=host',
                                '--variant=X%d' % bitness]
      if debug:
        libjdwp_common_command.append('--debug')
      if gcstress:
        libjdwp_common_command += ['--vm-arg', '-Xgc:gcstress']

      defer(api.step, 'test libjdwp jit', libjdwp_common_command)

      # Disable interpreter jdwp runs with gcstress, they time out.
      if not gcstress:
        defer(api.step, 'test libjdwp interpreter',
              libjdwp_common_command + ['--no-jit'])

def setup_target(api,
                 device,
                 debug,
                 build_only=False,
                 concurrent_collector=True,
                 gcstress=False,
                 generational_cc=True,
                 heap_poisoning=False,
                 on_virtual_machine=False,
                 manifest_branch="master-art"):

  build_top_dir = api.context.cwd
  art_tools = api.context.cwd.join('art', 'tools')
  # The path to the chroot directory on the device where ART and its
  # dependencies are installed, in case of chroot-based testing.
  chroot_dir='/data/local/art-test-chroot'

  qemu_path = ensure_tool(
    api=api,
    package='fuchsia/third_party/qemu/${platform}',
    version='integration',
    subdir='bin',
  )

  openssh_path = ensure_tool(
    api=api,
    package='fuchsia/third_party/openssh-portable/${platform}',
    version='latest',
    subdir='bin',
  )

  sevenz_path = ensure_tool(
    api=api,
    package='infra/3pp/tools/7z/${platform}',
    version='latest',
  )

  # This 7z package has a 7zz binary instead of a 7z binary, so this symlinks
  # from 7z to 7zz. This dependency is required for buildbot-vm.sh
  api.file.symlink(
    'symlink 7z to 7zz',
    api.path.join(sevenz_path, '7zz'),
    api.path.join(sevenz_path, '7z')
  )

  env = {
      'TARGET_BUILD_VARIANT':
          'eng',
      'TARGET_BUILD_TYPE':
          'release',
      'LANG':
          'en_US.UTF-8',
      'SOONG_ALLOW_MISSING_DEPENDENCIES':
          'true',
      'TARGET_BUILD_UNBUNDLED':
          'true',
      'ANDROID_BUILD_TOP':
          build_top_dir,
      'ADB':
          str(build_top_dir.join('prebuilts', 'runtime', 'adb')),
      'PATH':
          str(
              build_top_dir.join('prebuilts', 'jdk', 'jdk17', 'linux-x86',
                                 'bin')) + api.path.pathsep +
          # Add adb to the path.
          str(build_top_dir.join('prebuilts', 'runtime')) + api.path.pathsep +
          # Add 7z to the path.
          str(sevenz_path) + api.path.pathsep +
          # Add openssh-portable to the path.
          str(openssh_path) + api.path.pathsep +
          # Add qemu to the path.
          str(qemu_path) + api.path.pathsep +
          '%(PATH)s',
      'ART_TEST_RUN_TEST_2ND_ARCH':
          'false',
      'USE_DEX2OAT_DEBUG':
          'false',
      'ART_BUILD_HOST_DEBUG':
          'false',
      'ART_TEST_KEEP_GOING':
          'true'
  }

  if concurrent_collector:
    env.update({ 'ART_USE_READ_BARRIER' : 'true' })
  else:
    env.update({ 'ART_USE_READ_BARRIER' : 'false' })  # pragma: no cover

  # Note: Generational CC only makes sense when read barriers are used
  # (i.e. when the Concurrent Copying collector is used).
  if generational_cc:
    env.update({ 'ART_USE_GENERATIONAL_CC' : 'true' })
  else:
    env.update({ 'ART_USE_GENERATIONAL_CC' : 'false' })

  if heap_poisoning:
    env.update({ 'ART_HEAP_POISONING' : 'true' })
  else:
    env.update({ 'ART_HEAP_POISONING' : 'false' })

  if on_virtual_machine:
    env.update({
      'ART_TEST_SSH_USER': 'ubuntu',
      'ART_TEST_SSH_HOST': 'localhost',
      'ART_TEST_SSH_PORT': '10001',
      'ART_TEST_ON_VM': 'true',
    })

  bitness = _TARGET_DEVICE_MAP[device]['bitness']
  env.update(
      {'TARGET_PRODUCT': _TARGET_DEVICE_MAP[device]['product'],
       'ANDROID_PRODUCT_OUT': build_top_dir.join('out','target', 'product',
         _TARGET_DEVICE_MAP[device]['product'])
      })

  env.update({ 'ART_TEST_CHROOT' : chroot_dir })

  checkout(api,manifest_branch)
  clobber(api)

  gtest_env = env.copy()
  gtest_env.update({ 'ART_TEST_NO_SYNC': 'true' })

  test_env = gtest_env.copy()
  test_env.update({
      'PATH':
          str(build_top_dir.join('out', 'host', 'linux-x86', 'bin')) +
          api.path.pathsep + str(
              build_top_dir.join('prebuilts', 'jdk', 'jdk17', 'linux-x86',
                                 'bin')) + api.path.pathsep +
          # Add adb in the path.
          str(build_top_dir.join('prebuilts', 'runtime')) + api.path.pathsep +
          '%(PATH)s'
  })
  with api.context(env=env):
    api.step(
        'build target',
        [art_tools.join('buildbot-build.sh'), '--target', '--installclean'])

  if build_only:
    return

  if on_virtual_machine:
    with api.context(env=env):
      api.step(
        'create the virtual machine',
        [ art_tools.join('buildbot-vm.sh'), 'create' ]
      )

      api.step(
        'enable key authentication on virtual machine',
        [ art_tools.join('buildbot-vm.sh'), 'install-keys' ]
      )

      api.step(
        'boot the virtual machine',
        [ art_tools.join('buildbot-vm.sh'), 'boot' ]
      )

  with api.defer.context(collect_step_name=None) as defer:
    with api.context(env=test_env):
      defer(api.step, 'device pre-run cleanup',
            [art_tools.join('buildbot-cleanup-device.sh')])

      defer(api.step, 'setup device',
            [art_tools.join('buildbot-setup-device.sh'), '--verbose'])

    with api.context(env=env):
      defer(api.step, 'sync target', [art_tools.join('buildbot-sync.sh')])

    def test_logging(api, test_name):
      # adb doesn't know about the VM and will hang.
      if on_virtual_machine:
        return
      with api.context(env=test_env):
        defer(api.step, test_name + ': adb logcat',
              ['adb', 'logcat', '-d', '-v', 'threadtime'])
        defer(api.step, test_name + ': crashes',
              [art_tools.join('buildbot-symbolize-crashes.sh')])
        defer(api.step, test_name + ': adb clear log', ['adb', 'logcat', '-c'])


    with api.context(env=gtest_env):
      defer(api.step, 'test gtest', [art_tools.join('run-gtests.sh')])
    test_logging(api, 'test gtest')

    # Common options passed to testrunner.py.
    testrunner_cmd = [
        './art/test/testrunner/testrunner.py', '--target', '--verbose'
    ]

    if debug:
      testrunner_cmd += ['--debug']
    else:
      testrunner_cmd += ['--ndebug']

    if gcstress:
      testrunner_cmd += ['--gcstress']

    with api.context(env=test_env):
      defer(api.step, 'test optimizing', testrunner_cmd + ['--optimizing'])
    test_logging(api, 'test optimizing')

    with api.context(env=test_env):
      # We pass --optimizing for interpreter debuggable to run AOT checker tests
      # compiled debuggable.
      defer(api.step, 'test debuggable',
            testrunner_cmd + ['--optimizing', '--debuggable'])
    test_logging(api, 'test debuggable')

    with api.context(env=test_env):
      defer(api.step, 'test jit debuggable',
            testrunner_cmd + ['--jit', '--debuggable'])
    test_logging(api, 'test jit debuggable')

    with api.context(env=test_env):
      defer(api.step, 'test interpreter', testrunner_cmd + ['--interpreter'])
    test_logging(api, 'test interpreter')

    with api.context(env=test_env):
      defer(api.step, 'test baseline', testrunner_cmd + ['--baseline'])
    test_logging(api, 'test baseline')

    with api.context(env=test_env):
      defer(api.step, 'test jit', testrunner_cmd + ['--jit'])
    test_logging(api, 'test jit')

    with api.context(env=test_env):
      defer(api.step, 'test speed-profile',
            testrunner_cmd + ['--speed-profile'])
    test_logging(api, 'test speed-profile')

    libcore_command = [art_tools.join('run-libcore-tests.sh'),
                       '--mode=device',
                       '--variant=X%d' % bitness]
    if debug:
      libcore_command.append('--debug')
    if gcstress:
      libcore_command += ['--gcstress']
    # Ignore failures from Libcore tests using the getrandom() syscall (present
    # since Linux 3.17) on fugu devices, as they run a Linux 3.10 kernel.
    if device == 'fugu':
      libcore_command.append('--no-getrandom')

    # Disable libcore runs with gcstress and debug, they time out.
    if not (gcstress and debug):
      with api.context(env=test_env):
        defer(api.step, 'test libcore', libcore_command)
      test_logging(api, 'test libcore')

    libjdwp_command = [art_tools.join('run-libjdwp-tests.sh'),
                       '--mode=device',
                       '--variant=X%d' % bitness]
    if debug:
      libjdwp_command.append('--debug')
    if gcstress:
      libjdwp_command += ['--vm-arg', '-Xgc:gcstress']

    # Disable jit libjdwp runs with gcstress and debug, they time out.
    if not (gcstress and debug):
      with api.context(env=test_env):
        defer(api.step, 'test libjdwp jit', libjdwp_command)
      test_logging(api, 'test libjdwp jit')

    # Disable interpreter libjdwp runs with gcstress, they time out.
    if not gcstress:
      with api.context(env=test_env):
        defer(api.step, 'test libjdwp interpreter',
              libjdwp_command + ['--no-jit'])
      test_logging(api, 'test libjdwp interpreter')

    with api.context(env=test_env):
      defer(api.step, 'tear down device',
            [art_tools.join('buildbot-teardown-device.sh')])

      defer(api.step, 'device post-run cleanup',
            [art_tools.join('buildbot-cleanup-device.sh')])

    if on_virtual_machine:
      with api.context(env=env):
        defer(api.step, 'shut down virtual machine',
              [art_tools.join('buildbot-vm.sh'), 'quit'])

def GenTests(api):
  yield api.test(
    'host-x86_64-default_opts',
    api.buildbucket.ci_build(
      project='art',
    ),
    api.properties(
      bitness=32,
      debug=False,
      concurrent_collector=True,
      generational_cc=True,
    )
  )

  yield api.test(
    'host-x86_64-opts',
    api.buildbucket.ci_build(
      project='art',
    ),
    api.properties(
      bitness=64,
      debug=True,
      generational_cc=False,
      clobber='',
      concurrent_collector=False,
      heap_poisoning=True,
      gcstress=True,
      cdex_level='fast',
    )
  )

  yield api.test(
    'target_angler_try',
    api.buildbucket.try_build(
      project='art',
      builder='angler-armv7-ndebug',
    )
  )

  yield api.test(
    'target-default_opts',
    api.buildbucket.ci_build(
      project='art',
    ),
    api.properties(
      debug=False,
      device="angler-armv7",
      build_only=False,
      concurrent_collector=True,
      gcstress=False,
      generational_cc=True,
      heap_poisoning=False,
      on_virtual_machine=False,
    ),
  )

  yield api.test(
    'target-opts',
    api.buildbucket.ci_build(
      project='art',
    ),
    api.properties(
      debug=True,
      device="fugu",
      concurrent_collector=False,
      gcstress=True,
      generational_cc=False,
      heap_poisoning=True,
      on_virtual_machine=True,
    ),
  )

  yield api.test(
    'target-build_only',
    api.buildbucket.ci_build(
      project='art',
    ),
    api.properties(
      device="angler-armv7",
      build_only=True,
    ),
  )

  yield api.test(
      'target_angler_setup_failure',
      api.buildbucket.ci_build(
            project='art',
            builder='angler-armv7-ndebug',
      ),
      api.step_data('setup device', retcode=1),
      api.expect_status('FAILURE'),
      api.properties(
        bot_id='TestBot',
        device='angler-armv7',
        debug=False,
      )
  )

  yield api.test(
      'target_angler_device_pre_run_cleanup_failure',
      api.buildbucket.ci_build(
        project='art',
        builder='angler-armv7-ndebug',
      ),
      api.step_data('device pre-run cleanup', retcode=1),
      api.expect_status('FAILURE'),
      api.properties(
        bot_id='TestBot',
        device='angler-armv7',
        debug=False,
      )
  )
