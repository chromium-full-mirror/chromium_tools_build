# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.go.chromium.org.luci.buildbucket.proto import (
  builder_common as builder_common_pb2,
  builds_service as builds_service_pb2,
  common as common_pb2,
)
from PB.recipes.build.art import InputProperties

from contextlib import contextmanager
from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import repo
from RECIPE_MODULES.depot_tools import git
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cas,
  cipd,
  context,
  defer,
  file,
  path,
  properties,
  raw_io,
  runtime,
  step,
  time,
  url,
)


@dataclass
class DEPS(RecipeScriptApi):
  buildbucket: buildbucket.API
  cas: cas.API
  cipd: cipd.API
  context: context.API
  defer: defer.API
  file: file.API
  git: git.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  repo: repo.API
  runtime: runtime.API
  step: step.API
  time: time.API
  url: url.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  cipd: cipd.TEST_API
  context: context.TEST_API
  file: file.TEST_API
  git: git.TEST_API
  path: path.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  step: step.TEST_API
  time: time.TEST_API


# Value passed to option `-j` of command `repo sync`.
REPO_SYNC_JOBS = 16

PROPERTIES = InputProperties

HOST_TEST_INTERPRETER_MAKE_JOBS = 5


def RunSteps(api: DEPS, props):
  manifest_branch = props.manifest_branch or 'master-art'

  # Clear the content of legacy "art" cache to reclaim disk space.
  art_cache = api.path.cache_dir / 'art'
  if api.path.exists(art_cache):
    api.file.rmcontents('clean legacy art cache', art_cache)

  # Use different cache directory for RISCV to avoid interference.
  cache_name = 'builder' if props.device == 'qemu-riscv64' else 'src'
  if props.device:
    with api.context(cwd=api.path.cache_dir / cache_name):
      setup_target(
        api,
        device=props.device,
        product=props.product,
        build_only=props.build_only,
        on_virtual_machine=props.on_virtual_machine,
        repo_root=props.repo_root,
        manifest_branch=manifest_branch or 'master-art',
        test_steps=props.test_steps,
        test_env=props.test_env,
        gce_subbuilder=props.gce_subbuilder,
        output_directories=props.output_directories,
      )
  else:
    with api.context(cwd=api.path.cache_dir / cache_name):
      setup_host_x86(
        api,
        build_only=props.build_only,
        repo_root=props.repo_root,
        manifest_branch=manifest_branch or 'master-art',
        test_steps=props.test_steps,
        test_env=props.test_env,
      )


def setup_out(api: DEPS):
  out_dir = api.path.cache_dir / 'out'
  api.file.ensure_directory('ensure out dir', out_dir)
  src_out = api.context.cwd.joinpath('out')
  if not api.path.exists(src_out):
    api.file.symlink('symlink out dir', out_dir, src_out)


def checkout(api: DEPS, branch, repo_root):
  if 'art.superproject' in api.buildbucket.build.input.experiments:
    if api.path.exists(api.context.cwd.joinpath(".repo")):
      api.file.rmcontents("clean", api.context.cwd)  # Clean the other checkout.
    checkout_git(api, branch, repo_root or "https://android.googlesource.com")
  else:
    if api.path.exists(api.context.cwd.joinpath(".git")):
      api.file.rmcontents("clean", api.context.cwd)  # Clean the other checkout.
    checkout_repo(api, branch, repo_root or "https://android.googlesource.com")


def checkout_git(api: DEPS, branch, repo_root):
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
        # If the CL was just submitted, the super-project entry might not exist
        # yet.
        if retry > 0:
          api.time.sleep(delay * 60)
          api.git("fetch")
        cmd = api.git(
          *args,
          name="find super-project commit"
          + (f" (retry {retry})" if retry else ""),
          stdout=api.raw_io.output_text(),
        )
        if cmd.stdout:
          break
      assert cmd.stdout, f"Commit {ref} was not found in the git superproject"
      ref = cmd.stdout.strip().split("\n")[-1]

    api.git("checkout", "--force", ref)
    api.git("clean", "-ffxd", "-e", "out", "-e", "vm")
    api.git("show")
    api.step(
      "patch .gitmodules", ["sed", "-i", "/submodule/ s:/:-:g", ".gitmodules"]
    )
    api.git(
      "submodule",
      "update",
      "--init",
      "--force",
      "--depth",
      "1",
      "--jobs",
      "32",
      "--checkout",
    )
    api.git("status", "--ignore-submodules=none")

    for cl in api.buildbucket.build.input.gerrit_changes:
      git_dir = cl.project.replace("platform/", "")
      with api.context(cwd=api.context.cwd.joinpath(git_dir)):
        with api.step.nest(f'cherry-pick cl/{cl.change}/{cl.patchset}'):
          ref = f"refs/changes/{str(cl.change)[-2:]}/{cl.change}/{cl.patchset}"
          api.git("fetch", f"https://{cl.host}/{cl.project}", ref)
          api.git("cherry-pick", "FETCH_HEAD")


def checkout_repo(api: DEPS, manifest_branch, repo_root):
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


def clobber(api: DEPS):
  # buildbot sets 'clobber' to the empty string which is falsey, check with 'in'
  if 'clobber' in api.properties:
    api.file.rmtree('clobber', api.context.cwd.joinpath('out'))


# Calls api.cipd.ensure_tool, then modifies the path returned by the call to
# join the subdir.
def ensure_tool(api: DEPS, package, version, subdir=""):
  dirname = api.path.split(
    api.cipd.ensure_tool(
      package=package,
      version=version,
    )
  )[0]
  return api.path.abs_to_path(api.path.abspath(dirname.joinpath(subdir)))


def setup_host_x86(
  api: DEPS,
  build_only=False,
  repo_root=None,
  manifest_branch="master-art",
  test_steps=None,
  test_env=None,
):
  checkout(api, manifest_branch, repo_root)
  clobber(api)
  setup_out(api)

  build_top_dir = api.context.cwd
  art_tools = api.context.cwd.joinpath('art', 'tools')

  env = dict(test_env) if test_env else {}
  env.update(
    {
      'ANDROID_BUILD_TOP': build_top_dir,
      'PATH': str(build_top_dir.joinpath('out', 'host', 'linux-x86', 'bin'))
      + api.path.pathsep
      + str(
        build_top_dir.joinpath('prebuilts', 'jdk', 'jdk17', 'linux-x86', 'bin')
      )
      + api.path.pathsep
      + '%(PATH)s',
    }
  )

  with api.context(env=env):
    api.step(
      'build', [art_tools / 'buildbot-build.sh', '--host', '--installclean']
    )

    if build_only:
      return

    with api.defer.context() as defer:
      for step in test_steps or []:
        with api.context(env=env | dict(step.env)):
          defer(api.step, step.name, list(step.cmd))


def _archive_target_build(
  api: DEPS,
  build_top_dir,
  target_product,
  output_directories=None,
):
  tar_path = build_top_dir.joinpath('out', 'target_build.tar')
  api.step(
    'pack target build',
    [
      'tar',
      '-C',
      build_top_dir,
      '-cf',
      tar_path,
    ]
    + (
      list(output_directories)
      if output_directories
      else [
        f'out/target/product/{target_product}/system',
        f'out/target/product/{target_product}/data',
        f'out/target/product/{target_product}/linkerconfig',
        f'out/target/product/{target_product}/symbols',
        'out/target/common/obj/JAVA_LIBRARIES',
        'out/host/linux-x86/bin',
        'out/host/linux-x86/lib64',
        'out/host/linux-x86/framework',
        'out/host/linux-x86/etc',
      ]
    ),
  )
  digest = api.cas.archive(
    'archive target build to CAS',
    build_top_dir,
    tar_path,
  )
  export_step = api.step.empty('export cas_digest')
  export_step.presentation.properties['cas_digest'] = digest


def _get_gce_subbuild(api: DEPS, gce_subbuilder):
  commit = api.buildbucket.build.input.gitiles_commit
  tags = [
    common_pb2.StringPair(
      key='buildset',
      value=f'patch/gerrit/{c.host}/{c.change}/{c.patchset}',
    )
    for c in api.buildbucket.build.input.gerrit_changes
  ] + (
    [
      common_pb2.StringPair(
        key='buildset',
        value=f'commit/gitiles/{commit.host}/{commit.project}/+/{commit.id}',
      )
    ]
    if (commit.host and commit.project and commit.id)
    else []
  )
  existing = [
    b
    for b in api.buildbucket.search(
      builds_service_pb2.BuildPredicate(
        builder=builder_common_pb2.BuilderID(
          project=api.buildbucket.build.builder.project or 'art',
          bucket=api.buildbucket.build.builder.bucket or 'ci',
          builder=gce_subbuilder,
        ),
        tags=tags,
        include_experimental=api.buildbucket.build.input.experimental,
      ),
      limit=5,
      fields=['id', 'status', 'output.properties'],
      step_name='search existing GCE target build',
    )
    if b.status
    in (common_pb2.SUCCESS, common_pb2.SCHEDULED, common_pb2.STARTED)
  ]
  return (
    existing[0].id
    if existing
    else api.buildbucket.schedule(
      [
        api.buildbucket.schedule_request(
          builder=gce_subbuilder,
          as_shadow_if_parent_is_led=True,
          led_inherit_parent=True,
        )
      ],
      step_name='schedule GCE target subbuild',
    )[0].id
  )


def setup_target(
  api: DEPS,
  device=None,
  product=None,
  build_only=False,
  on_virtual_machine=False,
  repo_root=None,
  manifest_branch="master-art",
  test_steps=None,
  test_env=None,
  gce_subbuilder=None,
  output_directories=None,
):

  build_top_dir = api.context.cwd
  art_tools = api.context.cwd.joinpath('art', 'tools')

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
    api.path.join(sevenz_path, '7z'),
  )

  env = dict(test_env) if test_env else {}
  env.update(
    {
      'ANDROID_BUILD_TOP': build_top_dir,
      'ADB': str(build_top_dir.joinpath('prebuilts', 'runtime', 'adb')),
      'PATH': str(
        build_top_dir.joinpath('prebuilts', 'jdk', 'jdk17', 'linux-x86', 'bin')
      )
      + api.path.pathsep
      +
      # Add adb to the path.
      str(build_top_dir.joinpath('prebuilts', 'runtime'))
      + api.path.pathsep
      +
      # Add 7z to the path.
      str(sevenz_path)
      + api.path.pathsep
      +
      # Add openssh-portable to the path.
      str(openssh_path)
      + api.path.pathsep
      +
      # Add qemu to the path.
      str(qemu_path)
      + api.path.pathsep
      + '%(PATH)s',
    }
  )
  if product:
    env['ANDROID_PRODUCT_OUT'] = build_top_dir.joinpath(
      'out', 'target', 'product', product
    )

  gce_build_id = (
    _get_gce_subbuild(api, gce_subbuilder)
    if (gce_subbuilder and not build_only)
    else None
  )

  checkout(api, manifest_branch, repo_root)
  clobber(api)
  setup_out(api)

  device_test_env = env.copy()
  device_test_env.update(
    {
      'PATH': str(build_top_dir.joinpath('out', 'host', 'linux-x86', 'bin'))
      + api.path.pathsep
      + str(
        build_top_dir.joinpath('prebuilts', 'jdk', 'jdk17', 'linux-x86', 'bin')
      )
      + api.path.pathsep
      +
      # Add adb in the path.
      str(build_top_dir.joinpath('prebuilts', 'runtime'))
      + api.path.pathsep
      + '%(PATH)s'
    }
  )
  if gce_build_id:
    gce_build = api.buildbucket.collect_build(
      gce_build_id,
      step_name='wait for GCE target build',
      fields=['id', 'status', 'output.properties'],
      raise_if_unsuccessful=True,
      timeout=18000,  # 5 hours.
    )
    api.file.rmtree('clean out', build_top_dir.joinpath('out'))
    api.cas.download(
      'download target build from CAS',
      gce_build.output.properties['cas_digest'],
      build_top_dir,
    )
    api.step(
      'extract target build',
      [
        'tar',
        '-C',
        build_top_dir,
        '-xf',
        build_top_dir.joinpath('out', 'target_build.tar'),
      ],
    )
  else:
    with api.context(env=env):
      api.step(
        'build target',
        [art_tools.joinpath('buildbot-build.sh'), '--target', '--installclean'],
      )

    if build_only:
      _archive_target_build(
        api,
        build_top_dir,
        product or 'armv8',
        output_directories=output_directories,
      )

  if build_only:
    return

  if on_virtual_machine:
    with api.context(env=env):
      api.step(
        'create the virtual machine',
        [art_tools.joinpath('buildbot-vm.sh'), 'create'],
      )

      api.step(
        'enable key authentication on virtual machine',
        [art_tools.joinpath('buildbot-vm.sh'), 'install-keys'],
      )

      api.step(
        'boot the virtual machine',
        [art_tools.joinpath('buildbot-vm.sh'), 'boot'],
      )

  with api.defer.context() as defer:
    with api.context(env=device_test_env):
      defer(
        api.step,
        'device pre-run cleanup',
        [art_tools.joinpath('buildbot-cleanup-device.sh')],
      )

      defer(
        api.step,
        'setup device',
        [art_tools.joinpath('buildbot-setup-device.sh'), '--verbose'],
      )

    with api.context(env=env):
      defer(api.step, 'sync target', [art_tools.joinpath('buildbot-sync.sh')])

    def test_logging(api: DEPS, test_name):
      # adb doesn't know about the VM and will hang.
      if on_virtual_machine:
        return
      with api.context(env=device_test_env):
        defer(
          api.step,
          test_name + ': adb logcat',
          ['adb', 'logcat', '-d', '-v', 'threadtime'],
        )
        defer(
          api.step,
          test_name + ': crashes',
          [art_tools.joinpath('buildbot-symbolize-crashes.sh')],
        )
        defer(api.step, test_name + ': adb clear log', ['adb', 'logcat', '-c'])

    for step in test_steps or []:
      with api.context(env=device_test_env | dict(step.env)):
        defer(api.step, step.name, list(step.cmd))
      test_logging(api, step.name)

    with api.context(env=device_test_env):
      defer(
        api.step,
        'tear down device',
        [art_tools.joinpath('buildbot-teardown-device.sh')],
      )

      defer(
        api.step,
        'device post-run cleanup',
        [art_tools.joinpath('buildbot-cleanup-device.sh')],
      )

    if on_virtual_machine:
      with api.context(env=env):
        defer(
          api.step,
          'shut down virtual machine',
          [art_tools.joinpath('buildbot-vm.sh'), 'quit'],
        )


def GenTests(api: TEST_DEPS):
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
      test_env={
        'ART_HEAP_POISONING': 'false',
        'ART_TEST_KEEP_GOING': 'true',
        'ART_TEST_RUN_TEST_2ND_ARCH': 'false',
        'ART_USE_GENERATIONAL_CC': 'true',
        'ART_USE_READ_BARRIER': 'true',
        'BUILD_BROKEN_DISABLE_BAZEL': 'true',
        'HOST_PREFER_32_BIT': 'true',
        'LANG': 'en_US.UTF-8',
        'SOONG_ALLOW_MISSING_DEPENDENCIES': 'true',
        'TARGET_BUILD_TYPE': 'release',
        'TARGET_BUILD_UNBUNDLED': 'true',
        'TARGET_BUILD_VARIANT': 'eng',
        'TARGET_PRODUCT': 'armv8',
        'TARGET_RELEASE': 'trunk_staging',
      },
      test_steps=[
        {
          'name': 'test gtest',
          'cmd': [
            'build/soong/soong_ui.bash',
            '--make-mode',
            'test-art-host-gtest32',
          ],
        },
        {
          'name': 'test optimizing',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--verbose',
            '--host',
            '--ndebug',
            '--optimizing',
          ],
        },
        {
          'name': 'test debuggable',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--verbose',
            '--host',
            '--ndebug',
            '--jit',
            '--debuggable',
          ],
        },
        {
          'name': 'test interpreter',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--verbose',
            '--host',
            '--ndebug',
            '-j5',
            '--interpreter',
          ],
        },
        {
          'name': 'test baseline',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--verbose',
            '--host',
            '--ndebug',
            '--baseline',
          ],
        },
        {
          'name': 'test jit',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--verbose',
            '--host',
            '--ndebug',
            '--jit',
          ],
        },
        {
          'name': 'test speed-profile',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--verbose',
            '--host',
            '--ndebug',
            '--speed-profile',
          ],
        },
        {
          'name': 'test libcore',
          'cmd': [
            'art/tools/run-libcore-tests.sh',
            '--mode=host',
            '--variant=X32',
          ],
        },
        {
          'name': 'test libjdwp jit',
          'cmd': [
            'art/tools/run-libjdwp-tests.sh',
            '--mode=host',
            '--variant=X32',
          ],
        },
        {
          'name': 'test libjdwp interpreter',
          'cmd': [
            'art/tools/run-libjdwp-tests.sh',
            '--mode=host',
            '--variant=X32',
            '--no-jit',
          ],
        },
      ],
    ),
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
      continuousgc=True,
    ),
  )

  yield api.test(
    'target_angler_try',
    api.buildbucket.try_build(
      project='art',
      builder='angler-armv7-ndebug',
    ),
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
      bitness=32,
      product="arm_krait",
    ),
  )

  yield api.test(
    'target.arm.64',
    api.buildbucket.ci_build(
      project='art',
    ),
    api.properties(
      bitness=64,
      concurrent_collector=True,
      debug=True,
      device="target.arm.64",
      generational_cc=True,
      product="armv8",
      test_env={
        'ART_BUILD_HOST_DEBUG': 'false',
        'ART_HEAP_POISONING': 'false',
        'ART_TEST_CHROOT': '/data/local/art-test-chroot',
        'ART_TEST_KEEP_GOING': 'true',
        'ART_TEST_RUN_TEST_2ND_ARCH': 'false',
        'ART_USE_GENERATIONAL_CC': 'true',
        'ART_USE_READ_BARRIER': 'true',
        'LANG': 'en_US.UTF-8',
        'SOONG_ALLOW_MISSING_DEPENDENCIES': 'true',
        'TARGET_BUILD_TYPE': 'release',
        'TARGET_BUILD_UNBUNDLED': 'true',
        'TARGET_BUILD_VARIANT': 'eng',
        'TARGET_PRODUCT': 'armv8',
        'TARGET_RELEASE': 'trunk_staging',
        'USE_DEX2OAT_DEBUG': 'false',
      },
      test_steps=[
        {
          'name': 'test gtest',
          'cmd': ['art/tools/run-gtests.sh'],
          'env': {'ART_TEST_NO_SYNC': 'true'},
        },
        {
          'name': 'test optimizing',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--target',
            '--verbose',
            '--debug',
            '--optimizing',
          ],
        },
        {
          'name': 'test optimizing all-isa-features',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--target',
            '--verbose',
            '--debug',
            '--optimizing',
            '--run-test-option=--Xcompiler-option='
            '--instruction-set-variant=cortex-a35',
            '--run-test-option=--Xcompiler-option='
            '--instruction-set-features=runtime',
          ],
        },
        {
          'name': 'test debuggable',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--target',
            '--verbose',
            '--debug',
            '--optimizing',
            '--debuggable',
          ],
        },
        {
          'name': 'test jit debuggable',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--target',
            '--verbose',
            '--debug',
            '--jit',
            '--debuggable',
          ],
        },
        {
          'name': 'test interpreter',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--target',
            '--verbose',
            '--debug',
            '--interpreter',
          ],
        },
        {
          'name': 'test baseline',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--target',
            '--verbose',
            '--debug',
            '--baseline',
          ],
        },
        {
          'name': 'test jit',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--target',
            '--verbose',
            '--debug',
            '--jit',
          ],
        },
        {
          'name': 'test jit-on-first-use',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--target',
            '--verbose',
            '--debug',
            '--jit-on-first-use',
          ],
        },
        {
          'name': 'test speed-profile',
          'cmd': [
            './art/test/testrunner/testrunner.py',
            '--target',
            '--verbose',
            '--debug',
            '--speed-profile',
          ],
        },
        {
          'name': 'test libcore',
          'cmd': [
            'art/tools/run-libcore-tests.sh',
            '--mode=device',
            '--variant=X64',
            '--debug',
          ],
        },
        {
          'name': 'test libjdwp jit',
          'cmd': [
            'art/tools/run-libjdwp-tests.sh',
            '--mode=device',
            '--variant=X64',
            '--debug',
          ],
        },
        {
          'name': 'test libjdwp interpreter',
          'cmd': [
            'art/tools/run-libjdwp-tests.sh',
            '--mode=device',
            '--variant=X64',
            '--debug',
            '--no-jit',
          ],
        },
      ],
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
      continuousgc=True,
      generational_cc=False,
      heap_poisoning=True,
      on_virtual_machine=True,
      bitness=32,
      product="silvermont",
      test_steps=[
        {
          'name': 'test gtest',
          'cmd': ['art/tools/run-gtests.sh'],
        },
      ],
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
      bitness=32,
      product="arm_krait",
      output_directories=[
        'out/target/product/arm_krait/system',
        'out/target/product/arm_krait/data',
        'out/target/product/arm_krait/linkerconfig',
        'out/target/product/arm_krait/symbols',
        'out/target/common/obj/JAVA_LIBRARIES',
        'out/host/linux-x86/bin',
        'out/host/linux-x86/lib64',
        'out/host/linux-x86/framework',
        'out/host/linux-x86/etc',
      ],
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
      bitness=32,
      product="arm_krait",
    ),
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
      bitness=32,
      product="arm_krait",
    ),
  )

  yield api.test(
    'art.superproject-ci',  # tests gitiles_commit path.
    api.buildbucket.ci_build(experiments=['art.superproject']),
    api.step_data(
      "checkout.find super-project commit",
      stdout=api.raw_io.output_text("42424242"),
    ),
    api.properties(build_only=True),
  )

  yield api.test(
    'art.superproject-ci-retry',  # tests gitiles_commit path.
    api.buildbucket.ci_build(experiments=['art.superproject']),
    api.step_data(
      "checkout.find super-project commit", stdout=api.raw_io.output_text("")
    ),
    api.step_data(
      "checkout.find super-project commit (retry 1)",
      stdout=api.raw_io.output_text("42424242"),
    ),
    api.properties(build_only=True),
  )

  yield api.test(
    'art.superproject-ci-retry-2',  # tests gitiles_commit path.
    api.buildbucket.ci_build(experiments=['art.superproject']),
    api.step_data(
      "checkout.find super-project commit", stdout=api.raw_io.output_text("")
    ),
    api.step_data(
      "checkout.find super-project commit (retry 1)",
      stdout=api.raw_io.output_text(""),
    ),
    api.step_data(
      "checkout.find super-project commit (retry 2)",
      stdout=api.raw_io.output_text("42424242"),
    ),
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
    api.path.exists(api.path.cache_dir.joinpath("src/.git")),
    api.properties(build_only=True),
  )

  yield api.test(
    'art.superproject-repo2git',  # git checkout after repo checkout.
    api.buildbucket.ci_build(experiments=['art.superproject']),
    api.path.exists(api.path.cache_dir.joinpath("src/.repo")),
    api.step_data(
      "checkout.find super-project commit",
      stdout=api.raw_io.output_text("42424242"),
    ),
    api.properties(build_only=True),
  )

  yield api.test(
    'art.superproject-incremental',  # repeated git checkout.
    api.buildbucket.ci_build(experiments=['art.superproject']),
    api.path.exists(api.path.cache_dir.joinpath("src/.git")),
    api.step_data(
      "checkout.find super-project commit",
      stdout=api.raw_io.output_text("42424242"),
    ),
    api.properties(build_only=True),
  )

  yield api.test(
    'clean-legacy-art-cache',
    api.path.exists(api.path.cache_dir.joinpath("art")),
    api.properties(build_only=True),
  )

  completed_gce_build = api.buildbucket.ci_build_message(
    build_id=8922054662172514000,
    status='SUCCESS',
  )
  completed_gce_build.output.properties['cas_digest'] = (
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/0'
  )

  yield api.test(
    'target-docker-parent-gce-subbuild',
    api.buildbucket.ci_build(
      project='art',
      git_repo='https://googleplex-android.googlesource.com/platform/art',
      revision='2d2b87e5f9c872902d8508f6377470a4a6fa87e1',
    ),
    api.properties(
      device='target.arm.64',
      bitness=64,
      product='armv8',
      gce_subbuilder='target.arm.build_only.64',
      test_steps=[
        {
          'name': 'test gtest',
          'cmd': ['art/tools/run-gtests.sh'],
          'env': {'ART_TEST_NO_SYNC': 'true'},
        },
      ],
    ),
    api.buildbucket.simulated_collect_output(
      [completed_gce_build],
      step_name='wait for GCE target build',
    ),
  )
