# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.recipes.build.art import InputProperties

DEPS = [
    'depot_tools/git',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/defer',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'recipe_engine/step',
    'recipe_engine/time',
    'recipe_engine/url',
    'repo',
]


# Value passed to option `-j` of command `repo sync`.
REPO_SYNC_JOBS = 16

PROPERTIES = InputProperties

HOST_TEST_INTERPRETER_MAKE_JOBS = 5

def RunSteps(api, props):
  manifest_branch = props.manifest_branch or 'master-art'
  if props.device:
    # Use different cache directory for RISCV to avoid interference.
    cache_name = "builder" if props.device == 'qemu-riscv64' else "art"
    with api.context(cwd=api.path.cache_dir / cache_name):
      setup_target(
          api,
          device=props.device,
          bitness=props.bitness,
          product=props.product,
          debug=props.debug,
          build_only=props.build_only,
          concurrent_collector=props.concurrent_collector,
          generational_cc=props.generational_cc,
          heap_poisoning=props.heap_poisoning,
          gcstress=props.gcstress,
          continuousgc=props.continuousgc,
          on_virtual_machine=props.on_virtual_machine,
          repo_root=props.repo_root,
          manifest_branch=manifest_branch or 'master-art')
  else:
    with api.context(cwd=api.path.cache_dir / 'art'):
      setup_host_x86(
          api,
          debug=props.debug,
          bitness=props.bitness,
          build_only=props.build_only,
          concurrent_collector=props.concurrent_collector,
          generational_cc=props.generational_cc,
          heap_poisoning=props.heap_poisoning,
          gcstress=props.gcstress,
          continuousgc=props.continuousgc,
          repo_root=props.repo_root,
          manifest_branch=manifest_branch or 'master-art')


def checkout(api, branch, repo_root):
  if 'art.superproject' in api.buildbucket.build.input.experiments:
    if api.path.exists(api.context.cwd.joinpath(".repo")):
      api.file.rmcontents("clean", api.context.cwd)  # Clean the other checkout.
    checkout_git(api, branch, repo_root or "https://android.googlesource.com")
  else:
    if api.path.exists(api.context.cwd.joinpath(".git")):
      api.file.rmcontents("clean", api.context.cwd)  # Clean the other checkout.
    checkout_repo(api, branch, repo_root or "https://android.googlesource.com")


def checkout_git(api, branch, repo_root):
  with api.step.nest('checkout'):
    if api.path.exists(api.context.cwd.joinpath(".git")):
      api.git("fetch")
    else:
      url = repo_root + "/platform/superproject/master"
      api.git("clone", url, ".")

    ref = 'origin/' + branch
    if api.buildbucket.gitiles_commit.id:
      ref = api.buildbucket.gitiles_commit.id
      # Search for super-project commit that first mentions the given
      # sub-project commit (either as submodule or in .supermanifest).
      args = ["log", '--pretty=format:%H', f"-S{ref}", f"origin/{branch}"]
      for retry, delay in enumerate([0, 1, 2, 5, 10, 15]):
        # If the CL was just submitted, the super-project entry might not exist yet.
        if retry > 0:
          api.time.sleep(delay * 60)
          api.git("fetch")
        cmd = api.git(
            *args,
            name="find super-project commit" +
            (f" (retry {retry})" if retry else ""),
            stdout=api.raw_io.output_text())
        if cmd.stdout:
          break
      assert cmd.stdout, f"Commit {ref} was not found in the git superproject"
      ref = cmd.stdout.strip().split("\n")[-1]

    api.git("checkout", "--force", ref)
    api.git("clean", "-ffxd", "-e", "out", "-e", "vm")
    api.git("show")
    api.step("patch .gitmodules",
             ["sed", "-i", "/submodule/ s:/:-:g", ".gitmodules"])
    api.git("submodule", "update", "--init", "--force", "--depth", "1",
            "--jobs", "32", "--checkout")
    api.git("status", "--ignore-submodules=none")

    for cl in api.buildbucket.build.input.gerrit_changes:
      git_dir = cl.project.replace("platform/", "")
      with api.context(cwd=api.context.cwd.joinpath(git_dir)):
        with api.step.nest(f'cherry-pick cl/{cl.change}/{cl.patchset}'):
          ref = f"refs/changes/{str(cl.change)[-2:]}/{cl.change}/{cl.patchset}"
          api.git("fetch", f"https://{cl.host}/{cl.project}", ref)
          api.git("cherry-pick", "FETCH_HEAD")


def checkout_repo(api, manifest_branch, repo_root):
  # (https://crbug.com/1153114): do not attempt to update repo when
  # 'repo sync' runs.
  env = {'DEPOT_TOOLS_UPDATE': '0'}
  with api.context(env=env):
    api.repo.init(repo_root + '/platform/manifest', '-b', manifest_branch)
    api.repo.sync('-c', '-j%d' % (REPO_SYNC_JOBS), "--no-tags")

    build_input = api.buildbucket.build.input
    if build_input.gerrit_changes:
      for change in build_input.gerrit_changes:
        api.repo(
          ['download', change.project, f"{change.change}/{change.patchset}"],
          f"checkout change ref: {change.change}/{change.patchset}",
        )

    api.repo.manifest()


def clobber(api):
  # buildbot sets 'clobber' to the empty string which is falsey, check with 'in'
  if 'clobber' in api.properties:
    api.file.rmtree('clobber', api.context.cwd.joinpath('out'))

# Calls api.cipd.ensure_tool, then modifies the path returned by the call to
# join the subdir.
def ensure_tool(api, package, version, subdir=""):
  dirname = api.path.split(api.cipd.ensure_tool(
    package=package,
    version=version,
  ))[0]
  return api.path.abs_to_path(api.path.abspath(dirname.joinpath(subdir)))

def setup_host_x86(api,
                   debug,
                   bitness,
                   build_only,
                   concurrent_collector=True,
                   generational_cc=True,
                   heap_poisoning=False,
                   gcstress=False,
                   continuousgc=False,
                   repo_root=None,
                   manifest_branch="master-art"):
  checkout(api, manifest_branch, repo_root)
  clobber(api)

  build_top_dir = api.context.cwd
  art_tools = api.context.cwd.joinpath('art', 'tools')
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
          str(build_top_dir.joinpath('out', 'host', 'linux-x86', 'bin')) +
          api.path.pathsep + str(
              build_top_dir.joinpath('prebuilts', 'jdk', 'jdk17', 'linux-x86',
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

  if continuousgc:
    testrunner_cmd += ['--continuous-gc']

  with api.context(env=env):
    api.step('build',
             [art_tools / 'buildbot-build.sh', '--host', '--installclean'])

    if build_only:
      return

    with api.defer.context() as defer:
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

      defer(api.step, 'test speed-profile',
            testrunner_cmd + ['--speed-profile'])

      libcore_command = [
          art_tools.joinpath('run-libcore-tests.sh'), '--mode=host',
          '--variant=X%d' % bitness
      ]
      if debug:
        libcore_command.append('--debug')
      if gcstress:
        libcore_command += ['--gcstress']
      if continuousgc:
        libcore_command += ['--continuous-gc']

      defer(api.step, 'test libcore', libcore_command)

      libjdwp_run = art_tools.joinpath('run-libjdwp-tests.sh')
      libjdwp_common_command = [
          libjdwp_run, '--mode=host',
          '--variant=X%d' % bitness
      ]
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
                 bitness,
                 product,
                 debug,
                 build_only=False,
                 concurrent_collector=True,
                 gcstress=False,
                 continuousgc=False,
                 generational_cc=True,
                 heap_poisoning=False,
                 on_virtual_machine=False,
                 repo_root=None,
                 manifest_branch="master-art"):

  build_top_dir = api.context.cwd
  art_tools = api.context.cwd.joinpath('art', 'tools')
  # The path to the chroot directory on the device where ART and its
  # dependencies are installed, in case of chroot-based testing.
  if on_virtual_machine:
    chroot_dir = '/home/ubuntu/art-test-chroot'
  else:
    chroot_dir = '/data/local/art-test-chroot'

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
      'TARGET_RELEASE':
          'trunk_staging',
      'LANG':
          'en_US.UTF-8',
      'SOONG_ALLOW_MISSING_DEPENDENCIES':
          'true',
      'TARGET_BUILD_UNBUNDLED':
          'true',
      'ANDROID_BUILD_TOP':
          build_top_dir,
      'ADB':
          str(build_top_dir.joinpath('prebuilts', 'runtime', 'adb')),
      'PATH':
          str(
              build_top_dir.joinpath('prebuilts', 'jdk', 'jdk17', 'linux-x86',
                                     'bin')) + api.path.pathsep +
          # Add adb to the path.
          str(build_top_dir.joinpath('prebuilts', 'runtime')) +
          api.path.pathsep +
          # Add 7z to the path.
          str(sevenz_path) + api.path.pathsep +
          # Add openssh-portable to the path.
          str(openssh_path) + api.path.pathsep +
          # Add qemu to the path.
          str(qemu_path) + api.path.pathsep + '%(PATH)s',
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

  env.update({
      'TARGET_PRODUCT':
          product,
      'ANDROID_PRODUCT_OUT':
          build_top_dir.joinpath('out', 'target', 'product', product),
  })

  env.update({ 'ART_TEST_CHROOT' : chroot_dir })

  checkout(api, manifest_branch, repo_root)
  clobber(api)

  gtest_env = env.copy()
  gtest_env.update({ 'ART_TEST_NO_SYNC': 'true' })

  test_env = gtest_env.copy()
  test_env.update({
      'PATH':
          str(build_top_dir.joinpath('out', 'host', 'linux-x86', 'bin')) +
          api.path.pathsep + str(
              build_top_dir.joinpath('prebuilts', 'jdk', 'jdk17', 'linux-x86',
                                     'bin')) + api.path.pathsep +
          # Add adb in the path.
          str(build_top_dir.joinpath('prebuilts', 'runtime')) +
          api.path.pathsep + '%(PATH)s'
  })
  with api.context(env=env):
    api.step(
        'build target',
        [art_tools.joinpath('buildbot-build.sh'), '--target', '--installclean'])

  if build_only:
    return

  if on_virtual_machine:
    with api.context(env=env):
      api.step('create the virtual machine',
               [art_tools.joinpath('buildbot-vm.sh'), 'create'])

      api.step('enable key authentication on virtual machine',
               [art_tools.joinpath('buildbot-vm.sh'), 'install-keys'])

      api.step('boot the virtual machine',
               [art_tools.joinpath('buildbot-vm.sh'), 'boot'])

  with api.defer.context() as defer:
    with api.context(env=test_env):
      defer(api.step, 'device pre-run cleanup',
            [art_tools.joinpath('buildbot-cleanup-device.sh')])

      defer(api.step, 'setup device',
            [art_tools.joinpath('buildbot-setup-device.sh'), '--verbose'])

    with api.context(env=env):
      defer(api.step, 'sync target', [art_tools.joinpath('buildbot-sync.sh')])

    def test_logging(api, test_name):
      # adb doesn't know about the VM and will hang.
      if on_virtual_machine:
        return
      with api.context(env=test_env):
        defer(api.step, test_name + ': adb logcat',
              ['adb', 'logcat', '-d', '-v', 'threadtime'])
        defer(api.step, test_name + ': crashes',
              [art_tools.joinpath('buildbot-symbolize-crashes.sh')])
        defer(api.step, test_name + ': adb clear log', ['adb', 'logcat', '-c'])


    with api.context(env=gtest_env):
      defer(api.step, 'test gtest', [art_tools.joinpath('run-gtests.sh')])
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

    if continuousgc:
      testrunner_cmd += ['--continuous-gc']

    with api.context(env=test_env):
      defer(api.step, 'test optimizing', testrunner_cmd + ['--optimizing'])
    test_logging(api, 'test optimizing')

    if product == 'armv8':
      with api.context(env=test_env):
        defer(
            api.step, 'test optimizing all-isa-features', testrunner_cmd + [
                '--optimizing', '--run-test-option=--Xcompiler-option=' +
                '--instruction-set-features=-a53,crc,lse,fp16,-dotprod,-sve'
            ])
      test_logging(api, 'test optimizing all-isa-features')

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

    if bitness == 64:
      with api.context(env=test_env):
        defer(api.step, 'test jit-on-first-use',
              testrunner_cmd + ['--jit-on-first-use'])
      test_logging(api, 'test jit-on-first-use')

    with api.context(env=test_env):
      defer(api.step, 'test speed-profile',
            testrunner_cmd + ['--speed-profile'])
    test_logging(api, 'test speed-profile')

    libcore_command = [
        art_tools.joinpath('run-libcore-tests.sh'), '--mode=device',
        '--variant=X%d' % bitness
    ]
    if debug:
      libcore_command.append('--debug')
    if gcstress:
      libcore_command += ['--gcstress']
    if continuousgc:
      libcore_command += ['--continuous-gc']
    # Ignore failures from Libcore tests using the getrandom() syscall (present
    # since Linux 3.17) on fugu devices, as they run a Linux 3.10 kernel.
    if device == 'fugu':
      libcore_command.append('--no-getrandom')

    # Disable libcore runs with gcstress and debug, they time out.
    if not (gcstress and debug) and not on_virtual_machine:
      with api.context(env=test_env):
        defer(api.step, 'test libcore', libcore_command)
      test_logging(api, 'test libcore')

    libjdwp_command = [
        art_tools.joinpath('run-libjdwp-tests.sh'), '--mode=device',
        '--variant=X%d' % bitness
    ]
    if debug:
      libjdwp_command.append('--debug')
    if gcstress:
      libjdwp_command += ['--vm-arg', '-Xgc:gcstress']

    # Disable jit libjdwp runs with gcstress and debug, they time out.
    if not (gcstress and debug) and not on_virtual_machine:
      with api.context(env=test_env):
        defer(api.step, 'test libjdwp jit', libjdwp_command)
      test_logging(api, 'test libjdwp jit')

    # Disable interpreter libjdwp runs with gcstress, they time out.
    if not gcstress and not on_virtual_machine:
      with api.context(env=test_env):
        defer(api.step, 'test libjdwp interpreter',
              libjdwp_command + ['--no-jit'])
      test_logging(api, 'test libjdwp interpreter')

    with api.context(env=test_env):
      defer(api.step, 'tear down device',
            [art_tools.joinpath('buildbot-teardown-device.sh')])

      defer(api.step, 'device post-run cleanup',
            [art_tools.joinpath('buildbot-cleanup-device.sh')])

    if on_virtual_machine:
      with api.context(env=env):
        defer(api.step, 'shut down virtual machine',
              [art_tools.joinpath('buildbot-vm.sh'), 'quit'])

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
      'host-x86_64-opts', api.buildbucket.ci_build(project='art',),
      api.properties(
          bitness=64,
          debug=True,
          generational_cc=False,
          clobber='',
          concurrent_collector=False,
          heap_poisoning=True,
          gcstress=True,
          continuousgc=True,
      ))

  yield api.test(
    'target_angler_try',
    api.buildbucket.try_build(
      project='art',
      builder='angler-armv7-ndebug',
    )
  )

  yield api.test(
      'target-default_opts',
      api.buildbucket.ci_build(project='art',),
      api.properties(
          debug=False,
          device="angler-armv7",
          build_only=False,
          concurrent_collector=True,
          gcstress=False,
          generational_cc=True,
          heap_poisoning=False,
          on_virtual_machine=False,
          bitness=32,
          product="arm_krait",
      ),
  )

  yield api.test(
      'target.arm.64',
      api.buildbucket.ci_build(project='art',),
      api.properties(
          bitness=64,
          concurrent_collector=True,
          debug=True,
          device="target.arm.64",
          generational_cc=True,
          product="armv8",
      ),
  )

  yield api.test(
      'target-opts',
      api.buildbucket.ci_build(project='art',),
      api.properties(
          debug=True,
          device="fugu",
          concurrent_collector=False,
          gcstress=True,
          continuousgc=True,
          generational_cc=False,
          heap_poisoning=True,
          on_virtual_machine=True,
          bitness=32,
          product="silvermont",
      ),
  )

  yield api.test(
      'target-build_only',
      api.buildbucket.ci_build(project='art',),
      api.properties(
          device="angler-armv7",
          build_only=True,
          bitness=32,
          product="arm_krait",
      ),
  )

  yield api.test(
      'target_angler_setup_failure',
      api.buildbucket.ci_build(
          project='art',
          builder='angler-armv7-ndebug',
      ), api.step_data('setup device', retcode=1), api.expect_status('FAILURE'),
      api.properties(
          bot_id='TestBot',
          device='angler-armv7',
          debug=False,
          bitness=32,
          product="arm_krait",
      ))

  yield api.test(
      'target_angler_device_pre_run_cleanup_failure',
      api.buildbucket.ci_build(
          project='art',
          builder='angler-armv7-ndebug',
      ), api.step_data('device pre-run cleanup', retcode=1),
      api.expect_status('FAILURE'),
      api.properties(
          bot_id='TestBot',
          device='angler-armv7',
          debug=False,
          bitness=32,
          product="arm_krait",
      ))

  yield api.test(
      'art.superproject-ci',  # tests gitiles_commit path.
      api.buildbucket.ci_build(experiments=['art.superproject']),
      api.step_data(
          "checkout.find super-project commit",
          stdout=api.raw_io.output_text("42424242")),
      api.properties(build_only=True),
  )

  yield api.test(
      'art.superproject-ci-retry',  # tests gitiles_commit path.
      api.buildbucket.ci_build(experiments=['art.superproject']),
      api.step_data(
          "checkout.find super-project commit",
          stdout=api.raw_io.output_text("")),
      api.step_data(
          "checkout.find super-project commit (retry 1)",
          stdout=api.raw_io.output_text("42424242")),
      api.properties(build_only=True),
  )

  yield api.test(
      'art.superproject-ci-retry-2',  # tests gitiles_commit path.
      api.buildbucket.ci_build(experiments=['art.superproject']),
      api.step_data(
          "checkout.find super-project commit",
          stdout=api.raw_io.output_text("")),
      api.step_data(
          "checkout.find super-project commit (retry 1)",
          stdout=api.raw_io.output_text("")),
      api.step_data(
          "checkout.find super-project commit (retry 2)",
          stdout=api.raw_io.output_text("42424242")),
      api.properties(build_only=True),
  )

  yield api.test(
      'art.superproject-try',  # tests gerrit_changes path.
      api.buildbucket.try_build(experiments=['art.superproject']),
      api.properties(build_only=True),
  )

  yield api.test(
      'art.superproject-git2repo',  # repo checkout after git checkout.
      api.buildbucket.ci_build(experiments=[]),
      api.path.exists(api.path.cache_dir.joinpath("art/.git")),
      api.properties(build_only=True),
  )

  yield api.test(
      'art.superproject-repo2git',  # git checkout after repo checkout.
      api.buildbucket.ci_build(experiments=['art.superproject']),
      api.path.exists(api.path.cache_dir.joinpath("art/.repo")),
      api.step_data(
          "checkout.find super-project commit",
          stdout=api.raw_io.output_text("42424242")),
      api.properties(build_only=True),
  )

  yield api.test(
      'art.superproject-incremental',  # repeated git checkout.
      api.buildbucket.ci_build(experiments=['art.superproject']),
      api.path.exists(api.path.cache_dir.joinpath("art/.git")),
      api.step_data(
          "checkout.find super-project commit",
          stdout=api.raw_io.output_text("42424242")),
      api.properties(build_only=True),
  )
