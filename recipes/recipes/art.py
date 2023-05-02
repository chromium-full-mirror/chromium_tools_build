# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

DEPS = [
  'recipe_engine/buildbucket',
  'recipe_engine/context',
  'recipe_engine/cipd',
  'recipe_engine/file',
  'recipe_engine/path',
  'recipe_engine/properties',
  'recipe_engine/runtime',
  'recipe_engine/step',
  'repo',
]


# Value passed to option `-j` of command `repo sync`.
REPO_SYNC_JOBS = 16

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
        'product': 'qemu',
    },
}


def checkout(api):
  # (https://crbug.com/1153114): do not attempt to update repo when
  # 'repo sync' runs.
  env = {'DEPOT_TOOLS_UPDATE': '0'}
  with api.context(env=env):
    api.repo.init('https://android.googlesource.com/platform/manifest', '-b',
                  'master-art')
    api.repo.sync('-f', '-c', '-j%d' % (REPO_SYNC_JOBS), "--no-tags")
    api.repo.manifest()


def clobber(api):
  # buildbot sets 'clobber' to the empty string which is falsey, check with 'in'
  if 'clobber' in api.properties:
    api.file.rmtree('clobber', api.context.cwd.join('out'))

def ensure_qemu(api):
  api.cipd.ensure_tool(
      'fuchsia/third_party/qemu/${platform}', 'latest')

def setup_host_x86(api,
                   debug,
                   bitness,
                   concurrent_collector=True,
                   generational_cc=True,
                   heap_poisoning=False,
                   gcstress=False,
                   cdex_level='none'):
  checkout(api)
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

    with api.step.defer_results():
      api.step('test gtest', [
          'build/soong/soong_ui.bash', '--make-mode',
          'test-art-host-gtest%d' % bitness
      ])

      api.step('test optimizing', testrunner_cmd + ['--optimizing'])

      api.step('test debuggable', testrunner_cmd + ['--jit', '--debuggable'])

      # Use a lower `-j` number for interpreter, some tests take a long time
      # to run on it.
      api.step(
          'test interpreter', testrunner_cmd +
          ['-j%d' % (HOST_TEST_INTERPRETER_MAKE_JOBS), '--interpreter'])

      api.step('test baseline', testrunner_cmd + ['--baseline'])

      api.step('test jit', testrunner_cmd + ['--jit'])

      if cdex_level != "none":
        api.step(
            'test cdex-redefine-stress-optimizing', testrunner_cmd +
            ['--optimizing', '--redefine-stress', '--debuggable'])
        api.step(
            'test cdex-redefine-stress-jit',
            testrunner_cmd + ['--jit', '--redefine-stress', '--debuggable'])

      api.step('test speed-profile', testrunner_cmd + ['--speed-profile'])

      libcore_command = [art_tools.join('run-libcore-tests.sh'),
                         '--mode=host',
                         '--variant=X%d' % bitness]
      if debug:
        libcore_command.append('--debug')
      if gcstress:
        libcore_command += ['--gcstress']

      api.step('test libcore', libcore_command)

      libjdwp_run = art_tools.join('run-libjdwp-tests.sh')
      libjdwp_common_command = [libjdwp_run,
                                '--mode=host',
                                '--variant=X%d' % bitness]
      if debug:
        libjdwp_common_command.append('--debug')
      if gcstress:
        libjdwp_common_command += ['--vm-arg', '-Xgc:gcstress']

      api.step('test libjdwp jit', libjdwp_common_command)

      # Disable interpreter jdwp runs with gcstress, they time out.
      if not gcstress:
        api.step(
            'test libjdwp interpreter', libjdwp_common_command + ['--no-jit'])

def setup_target(api,
                 device,
                 debug,
                 concurrent_collector=True,
                 gcstress=False,
                 generational_cc=True,
                 heap_poisoning=False,
                 on_virtual_machine=False):
  build_top_dir = api.context.cwd
  art_tools = api.context.cwd.join('art', 'tools')
  # The path to the chroot directory on the device where ART and its
  # dependencies are installed, in case of chroot-based testing.
  chroot_dir='/data/local/art-test-chroot'

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
          # Add adb in the path.
          str(build_top_dir.join('prebuilts', 'runtime')) + api.path.pathsep +
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
      'ART_TEST_ON_VM': 'true'
    })
  else:
    env.update({ 'ART_TEST_ON_VM' : 'false' })


  bitness = _TARGET_DEVICE_MAP[device]['bitness']
  env.update(
      {'TARGET_PRODUCT': _TARGET_DEVICE_MAP[device]['product'],
       'ANDROID_PRODUCT_OUT': build_top_dir.join('out','target', 'product',
         _TARGET_DEVICE_MAP[device]['product'])
      })

  env.update({ 'ART_TEST_CHROOT' : chroot_dir })

  checkout(api)
  clobber(api)
  if on_virtual_machine:
    ensure_qemu(api)

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

  if on_virtual_machine:
    with api.context(env=env):
      api.step('create the virtual machine',
               [art_tools.join('buildbot-vm.sh'), 'create'])
      api.step('boot the virtual machine',
               [art_tools.join('buildbot-vm.sh'), 'boot'])
      api.step('copy ssh keys over to the virtual machine',
               [art_tools.join('buildbot-vm.sh'), 'setup-ssh'])

  with api.step.defer_results():
    with api.context(env=test_env):
      api.step('device pre-run cleanup',
               [art_tools.join('buildbot-cleanup-device.sh')])

      api.step('setup device',
               [art_tools.join('buildbot-setup-device.sh'), '--verbose'])

    with api.context(env=env):
      api.step('sync target', [art_tools.join('buildbot-sync.sh')])

    def test_logging(api, test_name):
      with api.context(env=test_env):
        api.step(test_name + ': adb logcat',
                 ['adb', 'logcat', '-d', '-v', 'threadtime'])
        api.step(test_name + ': crashes',
                 [art_tools.join('buildbot-symbolize-crashes.sh')])
        api.step(test_name + ': adb clear log', ['adb', 'logcat', '-c'])


    with api.context(env=gtest_env):
      api.step('test gtest', [art_tools.join('run-gtests.sh')])
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
      api.step('test optimizing', testrunner_cmd + ['--optimizing'])
    test_logging(api, 'test optimizing')

    with api.context(env=test_env):
      # We pass --optimizing for interpreter debuggable to run AOT checker tests
      # compiled debuggable.
      api.step('test debuggable',
               testrunner_cmd + ['--optimizing', '--debuggable'])
    test_logging(api, 'test debuggable')

    with api.context(env=test_env):
      api.step('test jit debuggable',
               testrunner_cmd + ['--jit', '--debuggable'])
    test_logging(api, 'test jit debuggable')

    with api.context(env=test_env):
      api.step('test interpreter', testrunner_cmd + ['--interpreter'])
    test_logging(api, 'test interpreter')

    with api.context(env=test_env):
      api.step('test baseline', testrunner_cmd + ['--baseline'])
    test_logging(api, 'test baseline')

    with api.context(env=test_env):
      api.step('test jit', testrunner_cmd + ['--jit'])
    test_logging(api, 'test jit')

    with api.context(env=test_env):
      api.step('test speed-profile', testrunner_cmd + ['--speed-profile'])
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
        api.step('test libcore', libcore_command)
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
        api.step('test libjdwp jit', libjdwp_command)
      test_logging(api, 'test libjdwp jit')

    # Disable interpreter libjdwp runs with gcstress, they time out.
    if not gcstress:
      with api.context(env=test_env):
        api.step('test libjdwp interpreter', libjdwp_command + ['--no-jit'])
      test_logging(api, 'test libjdwp interpreter')

    with api.context(env=test_env):
      api.step('tear down device',
               [art_tools.join('buildbot-teardown-device.sh')])

      api.step('device post-run cleanup',
               [art_tools.join('buildbot-cleanup-device.sh')])

    if on_virtual_machine:
      with api.context(env=env):
        api.step('shut down virtual machine',
                 [art_tools.join('buildbot-vm.sh'), 'quit'])


_CONFIG_MAP = {
    'x86': {
        'host-x86-ndebug': {
            'debug': False,
            'bitness': 32,
        },
        'host-x86-debug': {
            'debug': True,
            'bitness': 32,
        },
        'host-x86_64-ndebug': {
            'debug': False,
            'bitness': 64,
        },
        'host-x86_64-debug': {
            'debug': True,
            'bitness': 64,
        },
        # `userfaultfd`-based GC configuration.
        # TODO: Rename this configuration to reflect its properties.
        'host-x86-cms': {
            'debug': True,
            'bitness': 32,
            'concurrent_collector': False,
            'generational_cc': False,
        },
        # `userfaultfd`-based GC configuration.
        # TODO: Rename this configuration to reflect its properties.
        'host-x86_64-cms': {
            'debug': True,
            'bitness': 64,
            'concurrent_collector': False,
            'generational_cc': False,
        },
        'host-x86-poison-debug': {
            'debug': True,
            'bitness': 32,
            'heap_poisoning': True,
        },
        'host-x86_64-poison-debug': {
            'debug': True,
            'bitness': 64,
            'heap_poisoning': True,
        },
        'host-x86-gcstress-debug': {
            'bitness': 32,
            'debug': True,
            'gcstress': True,
        },
        'host-x86_64-non-gen-cc': {
            'bitness': 64,
            'debug': True,
            'generational_cc': False,
        },
        'host-x86_64-cdex-fast': {
            'debug': True,
            'bitness': 64,
            'cdex_level': 'fast',
        },
    },
    # TODO: Remove device names.
    'target': {
        'angler-armv7-ndebug': {
            'device': 'angler-armv7',
            'debug': False,
        },
        'angler-armv7-debug': {
            'device': 'angler-armv7',
            'debug': True,
        },
        'walleye-armv7-poison-debug': {
            'device': 'walleye-armv7',
            'debug': True,
            'heap_poisoning': True,
        },
        'walleye-armv8-poison-ndebug': {
            'device': 'walleye-armv8',
            'debug': False,
            'heap_poisoning': True,
        },
        'walleye-armv8-poison-debug': {
            'device': 'walleye-armv8',
            'debug': True,
            'heap_poisoning': True
        },
        'fugu-ndebug': {
            'device': 'fugu',
            'debug': False,
        },
        'fugu-debug': {
            'device': 'fugu',
            'debug': True,
        },
        # `userfaultfd`-based GC configuration.
        # TODO: Rename this configuration to reflect its properties.
        'angler-armv7-non-gen-cc': {
            'device': 'angler-armv7',
            'debug': True,
            'concurrent_collector': False,
            'generational_cc': False,
        },
        'angler-armv8-ndebug': {
            'device': 'angler-armv8',
            'debug': False,
        },
        'angler-armv8-debug': {
            'device': 'angler-armv8',
            'debug': True,
        },
        # `userfaultfd`-based GC configuration.
        # TODO: Rename this configuration to reflect its properties.
        'angler-armv8-non-gen-cc': {
            'device': 'angler-armv8',
            'debug': True,
            'concurrent_collector': False,
            'generational_cc': False,
        },
        'bullhead-armv8-gcstress-ndebug': {
            'device': 'bullhead-armv8',
            'debug': False,
            'gcstress': True,
        },
        'bullhead-armv8-gcstress-debug': {
            'device': 'bullhead-armv8',
            'debug': True,
            'gcstress': True,
        },
        'bullhead-armv7-gcstress-ndebug': {
            'device': 'bullhead-armv7',
            'debug': False,
            'gcstress': True,
        },
        'qemu-riscv64-ndebug': {
            'device': 'qemu-riscv64',
            'debug': False,
            'on_virtual_machine': True,
        },
    },
}

_CONFIG_DISPATCH_MAP = {
  'x86': setup_host_x86,
  'target': setup_target,
}

def RunSteps(api):
  builder_found = False
  buildername = api.buildbucket.builder_name
  for builder_type, builder_config in _CONFIG_MAP.items():
    if buildername in builder_config:
      builder_found = True
      builder_dict = builder_config[buildername]
      # Use the cached builder directory to enable incremental builds.
      with api.context(cwd=api.path['cache'].join('art')):
        _CONFIG_DISPATCH_MAP[builder_type](api, **builder_dict)
      break

  if not builder_found: # pragma: no cover
    error = "Builder not found in recipe's local config!"
    raise KeyError(error)

def GenTests(api):

  def build(builder):
    return sum([
        api.buildbucket.ci_build(
            project='art',
            builder=builder,
        ),
        api.properties(bot_id='TestSlave'),
    ], api.empty_test_data())

  for builders in _CONFIG_MAP.values():
    for buildername in builders:
      for clb in (None, True):
        yield api.test(
            '%s__%s' % (buildername, ('' if clb else 'no') + 'clobber'),
            build(buildername),
            (api.properties(clobber='') if clb else api.properties()),
        )

  yield api.test(
      'target_angler_setup_failure',
      build('angler-armv7-ndebug'),
      api.step_data('setup device', retcode=1),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'target_angler_device_pre_run_cleanup_failure',
      build('angler-armv7-ndebug'),
      api.step_data('device pre-run cleanup', retcode=1),
      api.expect_status('FAILURE'),
  )

#  This test *should* exist, but can't be included as it causes the recipe
#  simulation to error out, instead of showing that the build should become
#  purple instead. This may need to be fixed in the simulation test script.
#  yield api.test(
#     'invalid buildername',
#      api.properties(
#        mastername='client.art',
#        buildername='builder_does_not_exist',
#        bot_id='TestSlave',
#      ),
#    )
