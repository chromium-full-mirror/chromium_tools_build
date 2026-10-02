# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.go.chromium.org.luci.buildbucket.proto import (
  build as build_pb2,
  builder_common as builder_common_pb2,
  builds_service as builds_service_pb2,
  common as common_pb2,
)
from PB.recipes.build.art import InputProperties

import contextlib
from dataclasses import dataclass
import json
from urllib.parse import urlparse

from recipe_engine import post_process
from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from PB.go.chromium.org.luci.lucictx import sections as sections_pb2

from RECIPE_MODULES.depot_tools import git
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  cas,
  cipd,
  context,
  defer,
  file,
  led,
  path,
  properties,
  raw_io,
  resultdb,
  step,
  time,
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
  led: led.API
  path: path.API
  properties: properties.API
  raw_io: raw_io.API
  resultdb: resultdb.API
  step: step.API
  time: time.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  buildbucket: buildbucket.TEST_API
  cipd: cipd.TEST_API
  context: context.TEST_API
  file: file.TEST_API
  git: git.TEST_API
  led: led.TEST_API
  path: path.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  resultdb: resultdb.TEST_API
  step: step.TEST_API
  time: time.TEST_API


# Default base url to use for checkout.
DEFAULT_REPO_ROOT = 'https://googleplex-android.googlesource.com'

PROPERTIES = InputProperties


def RunSteps(api: DEPS, props):
  # Clear the content of legacy "art" cache to reclaim disk space.
  art_cache = api.path.cache_dir / 'art'
  if api.path.exists(art_cache):
    api.file.rmcontents('clean legacy art cache', art_cache)

  with api.context(cwd=api.path.cache_dir / 'src'):
    _run_steps(api, props)


def setup_out(api: DEPS):
  out_dir = api.path.cache_dir / 'out'
  if not api.path.exists(out_dir):
    api.file.ensure_directory('create out dir', out_dir)
  src_out = api.context.cwd.joinpath('out')
  api.step(
    'symlink out dir',
    ['bash', '-c', 'rm -rf "$1" && ln -s "$2" "$1"', '--', src_out, out_dir],
  )


def checkout(api: DEPS, branch, repo_root, commit):
  repo_root = repo_root or DEFAULT_REPO_ROOT
  with api.step.nest('checkout'):
    if api.path.exists(api.context.cwd.joinpath(".git")):
      api.git("fetch")
    else:
      url = repo_root + "/platform/superproject/master"
      api.git("clone", url, ".")

    # Search for super-project commit that first mentions the given
    # sub-project commit (either as submodule or in .supermanifest).
    args = ["log", '--pretty=format:%H', f"-S{commit.id}", f"origin/{branch}"]
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
    assert cmd.stdout, (
      f"Commit {commit.id} was not found in the git superproject"
    )
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


@contextlib.contextmanager
def _without_local_auth(api: DEPS):
  if not api.resultdb.enabled:
    yield
    return
  # Clear local_auth from LUCI_CONTEXT so `rdb stream` does not attach a
  # LUCI_CONTEXT OAuth token alongside the ResultDB invocation update-token.
  # ResultDB's global OAuth middleware rejects expired/transiently unverified
  # OAuth tokens with Unauthenticated before Recorder can check update-token.
  old_luci_context = api.context._state.luci_context
  api.context._state.luci_context = {
    **old_luci_context,
    'local_auth': sections_pb2.LocalAuth(),
  }
  try:
    yield
  finally:
    api.context._state.luci_context = old_luci_context


def _wrap_test_cmd(api: DEPS, step):
  cmd = list(step.cmd)
  if api.resultdb.enabled:
    return api.resultdb.wrap(
      cmd,
      base_variant={
        'builder': api.buildbucket.builder_name,
        'test_suite': step.name,
      },
    )
  return cmd


def _archive_target_build(
  api: DEPS,
  build_top_dir,
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
    + list(output_directories or ['out']),
  )
  digest = api.cas.archive(
    'archive target build to CAS',
    build_top_dir,
    tar_path,
  )
  export_step = api.step.empty('export cas_digest')
  export_step.presentation.properties['cas_digest'] = digest


def _get_art_commit(api: DEPS, repo_root):
  """Returns the build's gitiles_commit, or resolves ART HEAD if unset."""
  build_input = api.buildbucket.build.input
  if build_input.gitiles_commit.id:
    return build_input.gitiles_commit

  url = f'{repo_root or DEFAULT_REPO_ROOT}/platform/art'
  step = api.git(
    'ls-remote',
    '--symref',
    url,
    'HEAD',
    name='resolve ART HEAD',
    stdout=api.raw_io.output_text(),
  )
  # Output: "ref: <ref>\tHEAD" followed by "<sha>\tHEAD".
  symref_line, sha_line = step.stdout.strip().splitlines()
  ref = symref_line.split('\t')[0].removeprefix('ref: ')
  sha = sha_line.split('\t')[0]
  step.presentation.step_text = f'{ref} at {sha}'
  return common_pb2.GitilesCommit(
    host=urlparse(url).netloc, project='platform/art', ref=ref, id=sha
  )


def _get_gce_subbuild(api: DEPS, props, commit):
  build_input = api.buildbucket.build.input
  tags = [
    common_pb2.StringPair(
      key='buildset',
      value=f'patch/gerrit/{c.host}/{c.change}/{c.patchset}',
    )
    for c in build_input.gerrit_changes
  ]
  tags.append(
    common_pb2.StringPair(
      key='buildset',
      value=f'commit/gitiles/{commit.host}/{commit.project}/+/{commit.id}',
    )
  )
  # Under LED, tag subbuilds with the recipe CAS digest so that subbuild reuse
  # is specific to this exact recipe bundle revision.
  if api.led.rbe_cas_input and api.led.rbe_cas_input.digest.hash:
    tags.append(
      common_pb2.StringPair(
        key='recipe_cas',
        value=api.led.rbe_cas_input.digest.hash,
      )
    )

  # Tag search matches supersets (e.g. a CI build tagged with a commit would
  # match a try build tagged with the same commit plus a patch), so require
  # the relevant tags to match exactly.
  wanted_tags = {(t.key, t.value) for t in tags}

  def is_reusable(build):
    return build.status in (
      common_pb2.SUCCESS,
      common_pb2.SCHEDULED,
      common_pb2.STARTED,
    ) and wanted_tags == {
      (t.key, t.value)
      for t in build.tags
      if t.key in ('buildset', 'recipe_cas')
    }

  existing = [
    b
    for b in api.buildbucket.search(
      builds_service_pb2.BuildPredicate(
        builder=builder_common_pb2.BuilderID(
          project=api.buildbucket.build.builder.project or 'art',
          bucket=api.buildbucket.build.builder.bucket or 'ci',
          builder=props.gce_subbuilder,
        ),
        tags=tags,
        include_experimental=build_input.experimental,
      ),
      limit=5,
      fields=['id', 'status', 'tags'],
      step_name='search existing GCE target build',
    )
    if is_reusable(b)
  ]
  return (
    existing[0].id
    if existing
    else api.buildbucket.schedule(
      [
        api.buildbucket.schedule_request(
          builder=props.gce_subbuilder,
          gitiles_commit=commit,
          # Clear the property to prevent infinite recursion if the subbuilder
          # also has gce_subbuilder set.
          properties={'gce_subbuilder': ''},
          tags=tags,
          # All desired buildset tags are already in `tags`; avoid inheriting
          # extra parent buildset tags that would break the exact-match check in
          # is_reusable() above.
          inherit_buildsets=False,
          as_shadow_if_parent_is_led=True,
          led_inherit_parent=True,
        )
      ],
      step_name='schedule GCE target subbuild',
    )[0].id
  )


def _run_steps(api: DEPS, props):
  build_top_dir = api.context.cwd
  art_tools = api.context.cwd.joinpath('art', 'tools')

  env = dict(props.test_env)
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

  if props.device:
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

    env.update(
      {
        'ADB': str(build_top_dir.joinpath('prebuilts', 'runtime', 'adb')),
        'PATH': str(
          build_top_dir.joinpath(
            'prebuilts', 'jdk', 'jdk17', 'linux-x86', 'bin'
          )
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
    if props.product:
      env['ANDROID_PRODUCT_OUT'] = build_top_dir.joinpath(
        'out', 'target', 'product', props.product
      )

  commit = _get_art_commit(api, props.repo_root)
  gce_build_id = (
    _get_gce_subbuild(api, props, commit) if props.gce_subbuilder else None
  )

  checkout(api, props.manifest_branch or 'master-art', props.repo_root, commit)
  clobber(api)
  setup_out(api)

  device_test_env = env.copy()
  if props.device:
    device_test_env.update(
      {
        'PATH': str(build_top_dir.joinpath('out', 'host', 'linux-x86', 'bin'))
        + api.path.pathsep
        + str(
          build_top_dir.joinpath(
            'prebuilts', 'jdk', 'jdk17', 'linux-x86', 'bin'
          )
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
  elif props.device:
    with api.context(env=env):
      api.step(
        'build target',
        [art_tools.joinpath('buildbot-build.sh'), '--target', '--installclean'],
      )

    if props.build_only:
      _archive_target_build(
        api,
        build_top_dir,
        props.output_directories,
      )
  else:
    with api.context(env=env):
      api.step(
        'build',
        [art_tools.joinpath('buildbot-build.sh'), '--host', '--installclean'],
      )

  if props.build_only:
    return

  if props.on_virtual_machine:
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
    if props.device:
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
      if not props.device or props.on_virtual_machine:
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

    for step in props.test_steps:
      with (
        api.context(env=device_test_env | dict(step.env)),
        _without_local_auth(api),
      ):
        defer(api.step, step.name, _wrap_test_cmd(api, step))
      test_logging(api, step.name)

    if props.device:
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

    if props.on_virtual_machine:
      with api.context(env=env):
        defer(
          api.step,
          'shut down virtual machine',
          [art_tools.joinpath('buildbot-vm.sh'), 'quit'],
        )


def GenTests(api: TEST_DEPS):
  art_repo = 'https://googleplex-android.googlesource.com/platform/art'
  art_revision = '2d2b87e5f9c872902d8508f6377470a4a6fa87e1'
  random_commit_for_test = '42424242'

  yield api.test(
    'host-x86_64-default_opts',
    api.buildbucket.ci_build(
      project='art',
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(
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
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(
      clobber='',
    ),
  )

  yield api.test(
    'target_angler_try',
    api.buildbucket.try_build(
      project='art',
      builder='angler-armv7-ndebug',
    ),
    api.step_data(
      'resolve ART HEAD',
      stdout=api.raw_io.output_text(
        f'ref: refs/heads/main\tHEAD\n{art_revision}\tHEAD\n'
      ),
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
  )

  yield api.test(
    'target-default_opts',
    api.buildbucket.ci_build(
      project='art',
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(
      device="angler-armv7",
      build_only=False,
      on_virtual_machine=False,
      product="arm_krait",
    ),
  )

  yield api.test(
    'target.arm.64',
    api.buildbucket.ci_build(
      project='art',
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(
      device="target.arm.64",
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
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(
      device="fugu",
      on_virtual_machine=True,
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
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(
      device="angler-armv7",
      build_only=True,
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
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.step_data('setup device', retcode=1),
    api.expect_status('FAILURE'),
    api.properties(
      device='angler-armv7',
      product="arm_krait",
    ),
  )

  yield api.test(
    'target_angler_device_pre_run_cleanup_failure',
    api.buildbucket.ci_build(
      project='art',
      builder='angler-armv7-ndebug',
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.step_data('device pre-run cleanup', retcode=1),
    api.expect_status('FAILURE'),
    api.properties(
      device='angler-armv7',
      product="arm_krait",
    ),
  )

  yield api.test(
    'art.superproject-ci',  # tests gitiles_commit path.
    api.buildbucket.ci_build(),
    api.step_data(
      "checkout.find super-project commit",
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(build_only=True),
  )

  yield api.test(
    'art.superproject-ci-retry',  # tests gitiles_commit path.
    api.buildbucket.ci_build(),
    api.step_data(
      "checkout.find super-project commit", stdout=api.raw_io.output_text("")
    ),
    api.step_data(
      "checkout.find super-project commit (retry 1)",
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(build_only=True),
  )

  yield api.test(
    'art.superproject-ci-retry-2',  # tests gitiles_commit path.
    api.buildbucket.ci_build(),
    api.step_data(
      "checkout.find super-project commit", stdout=api.raw_io.output_text("")
    ),
    api.step_data(
      "checkout.find super-project commit (retry 1)",
      stdout=api.raw_io.output_text(""),
    ),
    api.step_data(
      "checkout.find super-project commit (retry 2)",
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(build_only=True),
  )

  yield api.test(
    'art.superproject-try',  # tests gerrit_changes path.
    api.buildbucket.try_build(),
    api.step_data(
      'resolve ART HEAD',
      stdout=api.raw_io.output_text(
        f'ref: refs/heads/main\tHEAD\n{art_revision}\tHEAD\n'
      ),
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(build_only=True),
  )

  yield api.test(
    'art.superproject-incremental',  # repeated git checkout.
    api.buildbucket.ci_build(),
    api.path.exists(api.path.cache_dir.joinpath("src/.git")),
    api.step_data(
      "checkout.find super-project commit",
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(build_only=True),
  )

  yield api.test(
    'clean-legacy-art-cache',
    api.path.exists(api.path.cache_dir.joinpath("art")),
    api.step_data(
      'resolve ART HEAD',
      stdout=api.raw_io.output_text(
        f'ref: refs/heads/main\tHEAD\n{art_revision}\tHEAD\n'
      ),
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(build_only=True),
  )

  commit_buildset = common_pb2.StringPair(
    key='buildset',
    value='commit/gitiles/googleplex-android.googlesource.com/platform/art/+/'
    + art_revision,
  )
  cas_digest = (
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855/0'
  )

  def _check_subbuild_request(check, steps, ref, commit_id):
    """Checks the subbuild is pinned to a commit and won't recurse."""
    req = json.loads(steps['schedule GCE target subbuild'].logs['request'])
    req = req['requests'][0]['scheduleBuild']
    check(req['properties'] == {'gce_subbuilder': ''})
    check(req['gitilesCommit']['ref'] == ref)
    check(req['gitilesCommit']['id'] == commit_id)
    check(f'-S{commit_id}' in steps['checkout.find super-project commit'].cmd)

  def completed_subbuild(build_id=8922054662172514000):
    build = api.buildbucket.ci_build_message(
      build_id=build_id,
      status='SUCCESS',
    )
    build.output.properties['cas_digest'] = cas_digest
    return build

  def collect_subbuild(build_id=8922054662172514000):
    return api.buildbucket.simulated_collect_output(
      [completed_subbuild(build_id)],
      step_name='wait for GCE target build',
    )

  def subbuild_props(**kwargs):
    props = {
      'device': 'target.arm.64',
      'product': 'armv8',
      'gce_subbuilder': 'target.arm.build_only.64',
      'test_steps': [
        {
          'name': 'test gtest',
          'cmd': ['art/tools/run-gtests.sh'],
          'env': {'ART_TEST_NO_SYNC': 'true'},
        },
      ],
    }
    props.update(kwargs)
    return api.properties(**props)

  yield api.test(
    'target-docker-parent-gce-subbuild',
    api.buildbucket.ci_build(
      project='art',
      git_repo=art_repo,
      revision=art_revision,
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    subbuild_props(),
    collect_subbuild(),
    api.post_process(post_process.DoesNotRun, 'resolve ART HEAD'),
    api.post_process(_check_subbuild_request, 'refs/heads/main', art_revision),
  )

  yield api.test(
    'subbuild-try-resolves-art-head',
    api.buildbucket.try_build(
      project='art',
      builder='target.arm.64',
    ),
    api.step_data(
      'resolve ART HEAD',
      stdout=api.raw_io.output_text(
        f'ref: refs/heads/main\tHEAD\n{art_revision}\tHEAD\n'
      ),
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    subbuild_props(),
    collect_subbuild(),
    api.post_process(post_process.MustRun, 'resolve ART HEAD'),
    api.post_process(_check_subbuild_request, 'refs/heads/main', art_revision),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'subbuild-build_only',
    api.buildbucket.ci_build(
      project='art',
      git_repo=art_repo,
      revision=art_revision,
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    subbuild_props(build_only=True),
    collect_subbuild(),
    api.post_process(
      post_process.MustRun,
      'wait for GCE target build',
      'download target build from CAS',
      'extract target build',
    ),
    api.post_process(
      post_process.DoesNotRun,
      'build target',
      'archive target build to CAS',
      'export cas_digest',
      'test gtest',
    ),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'subbuild-reuse-exact-match',
    api.buildbucket.ci_build(
      project='art',
      git_repo=art_repo,
      revision=art_revision,
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    subbuild_props(),
    api.buildbucket.simulated_search_results(
      [
        # Same commit, but with an extra patch: must not be reused.
        build_pb2.Build(
          id=8922054662172514001,
          status=common_pb2.SUCCESS,
          tags=[
            commit_buildset,
            common_pb2.StringPair(
              key='buildset',
              value='patch/gerrit/android-review.googlesource.com/123/1',
            ),
          ],
        ),
        # Exact match, but failed: must not be reused.
        build_pb2.Build(
          id=8922054662172514002,
          status=common_pb2.FAILURE,
          tags=[commit_buildset],
        ),
        # Exact match: reused.
        build_pb2.Build(
          id=8922054662172514003,
          status=common_pb2.STARTED,
          tags=[
            commit_buildset,
            common_pb2.StringPair(key='user_agent', value='recipe'),
          ],
        ),
      ],
      step_name='search existing GCE target build',
    ),
    collect_subbuild(8922054662172514003),
    api.post_process(post_process.DoesNotRun, 'schedule GCE target subbuild'),
    api.post_process(post_process.MustRun, 'download target build from CAS'),
    api.post_process(post_process.DropExpectation),
  )

  yield api.test(
    'target-docker-parent-gce-subbuild-led',
    api.buildbucket.ci_build(
      project='art',
      git_repo=art_repo,
      revision=art_revision,
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(
      **{
        '$recipe_engine/led': {
          'led_run_id': 'fake-run-id',
          'rbe_cas_input': {
            'cas_instance': (
              'projects/chromium-swarm/instances/default_instance'
            ),
            'digest': {
              'hash': 'examplehash',
              'size_bytes': 71,
            },
          },
        },
        'device': 'target.arm.64',
        'product': 'armv8',
        'gce_subbuilder': 'target.arm.build_only.64',
        'test_steps': [
          {
            'name': 'test gtest',
            'cmd': ['art/tools/run-gtests.sh'],
            'env': {'ART_TEST_NO_SYNC': 'true'},
          },
        ],
      }
    ),
    collect_subbuild(),
  )

  yield api.test(
    'host-x86_64-no-resultdb',
    api.context.luci_context(resultdb=sections_pb2.ResultDB()),
    api.step_data(
      'resolve ART HEAD',
      stdout=api.raw_io.output_text(
        f'ref: refs/heads/main\tHEAD\n{art_revision}\tHEAD\n'
      ),
    ),
    api.step_data(
      'checkout.find super-project commit',
      stdout=api.raw_io.output_text(random_commit_for_test),
    ),
    api.properties(
      test_steps=[
        {
          'name': 'test gtest',
          'cmd': [
            'build/soong/soong_ui.bash',
            '--make-mode',
            'test-art-host-gtest64',
          ],
        },
      ],
    ),
  )
