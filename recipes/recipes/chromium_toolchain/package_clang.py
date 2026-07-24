# Copyright 2016 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import post_process
from recipe_engine.engine_types import freeze

from PB.recipes.build.chromium_toolchain.package import InputProperties
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from RECIPE_MODULES.build import chromium_types

DEPS = [
    'chromium',
    'chromium_checkout',
    'depot_tools/depot_tools',
    'depot_tools/gsutil',
    'depot_tools/osx_sdk',
    'recipe_engine/buildbucket',
    'recipe_engine/cipd',
    'recipe_engine/context',
    'recipe_engine/futures',
    'recipe_engine/json',
    'recipe_engine/path',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/runtime',
    'recipe_engine/step',
]

PROPERTIES = InputProperties

BUILDERS = {
    'tryserver.chromium.linux': {
        'builders': {
            'linux_upload_clang':
                chromium_types.BuilderSpec.create(
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'linux',
                        'TARGET_BITS': 64,
                    },
                    gclient_apply_config=[
                        # 'android' is required to build the Clang toolchain with
                        # proper AddressSanitizer prebuilts for Chrome on Android.
                        'android',

                        # Required to build the builtins.a for Fuchsia.
                        'fuchsia_no_hooks',
                    ],
                ),
        },
    },
    'tryserver.chromium.mac': {
        'builders': {
            'mac_upload_clang':
                chromium_types.BuilderSpec.create(
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'mac',
                        'TARGET_BITS': 64,
                    },),
            'mac_upload_clang_arm':
                chromium_types.BuilderSpec.create(
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'mac',
                        'TARGET_BITS': 64,
                    },),
        },
    },
    'tryserver.chromium.win': {
        'builders': {
            'win_upload_clang':
                chromium_types.BuilderSpec.create(
                    chromium_config_kwargs={
                        'BUILD_CONFIG': 'Release',
                        'TARGET_PLATFORM': 'win',
                        'TARGET_BITS': 32,
                    },),
        },
    },
}

# Copy bot config for toolchain packagers.
BUILDERS['official.toolchain'] = {
    'builders': {
        'toolchain-packager-linux':
            BUILDERS['tryserver.chromium.linux']['builders']
            ['linux_upload_clang'],
        'toolchain-packager-mac':
            BUILDERS['tryserver.chromium.mac']['builders']['mac_upload_clang'],
        'toolchain-packager-mac-arm':
            BUILDERS['tryserver.chromium.mac']['builders']
            ['mac_upload_clang_arm'],
        'toolchain-packager-windows':
            BUILDERS['tryserver.chromium.win']['builders']['win_upload_clang'],
    },
}

BUILDERS = freeze(BUILDERS)

# GCS bucket where the official packagers upload archives.
GCS_BUCKET_PROD = 'chromium-browser-toolchain-prod'


def _trigger_tbi(api, change=None):
  with api.step.nest("package clang using TBI"):
    try:
      tbi_client = api.cipd.ensure_tool(
          "infra_internal/tools/security/lexan_tbi_client/${platform}",
          "latest")
      env = {}
      if change:
        env = {
            'GERRIT_HOST': str(change.host),
            'GERRIT_PROJECT': str(change.project),
            'GERRIT_CHANGE_ID': str(change.change),
            'GERRIT_PATCHSET_ID': str(change.patchset),
        }
      with api.context(env=env):
        res = api.step('request build', [
            tbi_client,
            '--pool',
            'high-cpu',
            "--result_file",
            api.json.output(name="summary"),
            "--clang",
        ])
      if res.exc_result.retcode == 0:
        res.presentation.step_text = 'TBI finished successfully'
    except (api.step.StepFailure, api.step.InfraFailure) as e:
      api.step.active_result.presentation.step_text = (
          "TBI failed or infra issue: %s" % e)

def RunSteps(api, properties):
  _, bot_config = api.chromium.configure_bot(BUILDERS)

  update_result = api.chromium_checkout.ensure_checkout(
      clobber=bot_config.clobber)
  source_dir = update_result.source_root.path

  api.step(
      'update win toolchain',
      ['python3',
       source_dir.joinpath('build', 'vs_toolchain.py'), 'update'])

  with api.osx_sdk('ios'):
    with api.depot_tools.on_path():
      args = ['--upload']
      if api.buildbucket.builder_name in BUILDERS['official.toolchain'][
          'builders'].keys():
        args += ['--bucket', GCS_BUCKET_PROD]
      if properties.llvm_revision:
        args += ['--revision', properties.llvm_revision]
      # Run a TBI build job in the background only for linux try jobs.
      tbi_background = None
      if (not properties.disable_tbi and
          'linux_upload_clang' in api.buildbucket.builder_name):
        changes = api.buildbucket.build.input.gerrit_changes
        change = None
        if changes:
          change = changes[0]
        tbi_background = api.futures.spawn_immediate(_trigger_tbi, api, change)
      try:
        api.step('package clang', [
            'python3',
            source_dir.joinpath('tools', 'clang', 'scripts', 'package.py')
        ] + args)
      finally:
        clang_crashreports_script = source_dir.joinpath(
            'tools', 'clang', 'scripts', 'process_crashreports.py')
        if api.path.exists(clang_crashreports_script):
          source = 'package_clang-%s' % (api.buildbucket.builder_name)
          if api.buildbucket.build.number:
            source += '-%s' % api.buildbucket.build.number

          crash_step = api.step(
              'process clang crashes',
              ['python3', clang_crashreports_script, '--source', source],
              stdout=api.raw_io.output_text(),
              step_test_data=lambda: api.raw_io.test_api.stream_output_text(
                  '    gs://chrome-clang-crash-reports/v1/2023/01/01/user-base.tgz\n'
              ))
          if crash_step and crash_step.stdout is not None:
            crash_step.presentation.logs['stdout'] = crash_step.stdout
            for line in crash_step.stdout.splitlines():
              line = line.strip()
              if line.startswith('gs://'):
                url = line.replace('gs://', 'https://storage.cloud.google.com/')
                filename = line.split('/')[-1]
                crash_step.presentation.links[filename] = url
      # Cancel the background TBI job if package clang has finished, TBI for
      # the time being should be non blocking.
      if tbi_background:
        if not tbi_background.done and not api._test_data.enabled:
          tbi_background.cancel()  # pragma: no cover
          tbi_background.result()  # pragma: no cover

def GenTests(api):
  yield api.test(
      'mac',
      api.platform.name('mac'),
      api.chromium.try_build(
          builder_group='tryserver.chromium.mac',
          builder='mac_upload_clang_arm'),
      api.post_process(post_process.MustRun, 'install xcode'),
      api.post_process(post_process.MustRun, 'select XCode'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux',
      api.platform.name('linux'),
      api.chromium.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux_upload_clang'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux-tbi-success',
      api.properties(disable_tbi=False),
      api.platform.name('linux'),
      api.chromium.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux_upload_clang',
          gerrit_changes=[
              common_pb2.GerritChange(
                  host='chromium-review.googlesource.com',
                  project='fake-project',
                  change=123456,
                  patchset=16)
          ]),
      api.post_process(post_process.StatusSuccess),
      api.override_step_data(
          'package clang using TBI.request build',
          api.json.output(name='summary', data={'test': 'data'})),
      api.post_process(post_process.MustRun,
                       'package clang using TBI.request build'),
      api.post_process(post_process.StepEnvContains,
                       'package clang using TBI.request build', {
                           'GERRIT_CHANGE_ID': '123456',
                       }),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'linux-tbi-failure',
      api.properties(disable_tbi=False),
      api.platform.name('linux'),
      api.chromium.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux_upload_clang'),
      api.step_data('package clang using TBI.request build', retcode=1),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.DropExpectation),
  )

  yield api.test(
      'package_fail',
      api.platform.name('linux'),
      api.chromium.try_build(
          builder_group='tryserver.chromium.linux',
          builder='linux_upload_clang'),
      api.step_data('package clang', retcode=1),
      api.path.exists(
          api.path.cache_dir /
          'builder/src/tools/clang/scripts/process_crashreports.py'),
      api.post_process(post_process.MustRun, 'process clang crashes'),
      api.post_process(post_process.DropExpectation),
      api.expect_status('FAILURE'),
  )

  yield api.test(
      'official',
      api.platform.name('linux'),
      api.chromium.ci_build(
          builder_group='official.toolchain',
          builder='toolchain-packager-linux'),
      api.properties(llvm_revision='abcd'),
      api.post_process(post_process.StatusSuccess),
      api.post_process(post_process.StepCommandRE, 'package clang', [
          'python3', '.*/tools/clang/scripts/package.py', '--upload',
          '--bucket', GCS_BUCKET_PROD, '--revision', 'abcd'
      ]),
      api.post_process(post_process.DropExpectation),
  )
