# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# pylint: disable=line-too-long

from recipe_engine import post_process, recipe_api

import contextlib
import json
import re

DEPS = [
    'chromium',
    'depot_tools/bot_update',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/git',
    'depot_tools/gsutil',
    'recipe_engine/buildbucket',
    'recipe_engine/context',
    'recipe_engine/defer',
    'recipe_engine/file',
    'recipe_engine/path',
    'recipe_engine/scheduler',
    'recipe_engine/platform',
    'recipe_engine/properties',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'recipe_engine/url',
]

# Sometimes a revision will be bad because the checkout will fail, causing
# publish_tarball to fail.  The version will stay in the version list for
# several months and publish_tarball will keep re-running on the same broken
# version.  This denylist exists to exclude those broken versions so the bot
# doesn't keep retrying and sending build failure emails out.
DENYLISTED_VERSIONS = [
    # crbug.com/1493085: https://crrev.com/c/4952605 introduced a cherry-pick
    # from a rust-lang/rust GitHub fork, but the commit was force-pushed on
    # 2023-10-26 and the "download rustc sources" step was broken until
    # https://crrev.com/c/4983487, which is part of 120.0.6098.0.
    '120.0.6087.2',
    '120.0.6089.1',
    '120.0.6089.2',
    '120.0.6089.3',
    '120.0.6090.0',
    '120.0.6090.1',
    '120.0.6091.0',
    '120.0.6091.1',
    '120.0.6091.2',
    '120.0.6091.3',
    '120.0.6091.4',
    '120.0.6091.6',
    '120.0.6092.0',
    '120.0.6092.1',
    '120.0.6093.0',
    '120.0.6093.1',
    '120.0.6094.0',
    '120.0.6094.1',
    '120.0.6095.0',
    '120.0.6095.1',
    '120.0.6096.0',
    '120.0.6096.1',
    '120.0.6097.0',
    '120.0.6097.1',
    # crbug.com/374696520: https://crrev.com/c/5926131 (#1367659) changed a
    # cherry-pick to use a revision that did not apply cleanly with the current
    # RUST_REVISION, breaking the "download rustc sources" step. This was fixed
    # on trunk in https://crrev.com/c/5952413 (#1372923) which updated the
    # RUST_REVISION, and on the M131 branch in https://crrev.com/c/6029791.
    '131.0.6772.0',
    '131.0.6772.1',
    '131.0.6773.0',
    '131.0.6773.1',
    '131.0.6774.0',
    '131.0.6774.1',
    '131.0.6775.0',
    '131.0.6775.1',
    '131.0.6776.0',
    '131.0.6776.1',
    '131.0.6777.0',
    '131.0.6777.1',
    '131.0.6777.2',
    '131.0.6778.0',
    '131.0.6778.1',
    '131.0.6778.2',
    '131.0.6778.3',
    '131.0.6778.4',
    '131.0.6778.5',
    '131.0.6778.6',
    '131.0.6778.7',
    '131.0.6778.8',
    '131.0.6778.9',
    '131.0.6778.10',
    '131.0.6778.11',
    '131.0.6778.12',
    '131.0.6778.13',
    '131.0.6778.14',
    '131.0.6778.15',
    '131.0.6778.16',
    '131.0.6778.17',
    '131.0.6778.18',
    '131.0.6778.19',
    '131.0.6778.20',
    '131.0.6778.21',
    '131.0.6778.22',
    '131.0.6778.23',
    '131.0.6778.24',
    '131.0.6778.25',
    '131.0.6778.26',
    '131.0.6778.27',
    '131.0.6778.28',
    '131.0.6778.29',
    '131.0.6778.30',
    '131.0.6778.31',
    '131.0.6778.32',
    '131.0.6778.33',
    '131.0.6778.34',
    '131.0.6778.35',
    '131.0.6778.36',
    '131.0.6778.37',
    '131.0.6778.38',
    '131.0.6778.39',
    '131.0.6778.40',
    '131.0.6778.41',
    '131.0.6778.42',
    '131.0.6778.43',
    '131.0.6778.44',
    '131.0.6778.45',
    '131.0.6778.46',
    '131.0.6778.47',
    '131.0.6778.48',
    '131.0.6778.49',
    '131.0.6778.50',
    '131.0.6778.51',
    '131.0.6778.52',
    '131.0.6778.53',
    '131.0.6778.54',
    '131.0.6778.55',
    '131.0.6778.56',
    '131.0.6778.57',
    '131.0.6778.58',
    '131.0.6778.59',
    '131.0.6778.60',
    '131.0.6778.61',
    '131.0.6778.62',
    '131.0.6778.63',
    '131.0.6778.64',
    '131.0.6778.65',
    '131.0.6778.66',
    '131.0.6778.67',
    '131.0.6778.68',
    '131.0.6778.69',
    '131.0.6778.70',
    '131.0.6778.71',
    '131.0.6778.72',
    '131.0.6778.73',
    '131.0.6778.74',
    '131.0.6778.75',
    '131.0.6778.80',
    '132.0.6779.0',
    '132.0.6779.1',
    '132.0.6780.0',
    '132.0.6780.1',
    '132.0.6781.0',
    '132.0.6781.1',
    '132.0.6782.0',
    '132.0.6782.1',
    '132.0.6783.0',
    '132.0.6783.1',
    '132.0.6784.0',
    '132.0.6784.1',
    '132.0.6784.2',
    '132.0.6785.0',
    '132.0.6785.1',
    '132.0.6786.0',
    '132.0.6786.1',
    '132.0.6787.0',
    '132.0.6787.1',
    '132.0.6788.0',
    '132.0.6788.1',
    '132.0.6789.0',
    '132.0.6789.1',
    '132.0.6790.0',
    '132.0.6790.1',
    '132.0.6790.2',
    '132.0.6791.0',
    '132.0.6791.1',
    '132.0.6792.0',
    '132.0.6792.2',
    '132.0.6793.0',
    '132.0.6793.1',
    '132.0.6793.2',
    '132.0.6793.3',
    '132.0.6794.0',
    '132.0.6794.1',
    '132.0.6794.2',
    '132.0.6795.0',
    # crbug.com/433513424: https://crrev.com/c/6781967 (140.0.7317.0)
    # introduced a Rust cherry-pick that did not apply. This was fixed in
    # https://crrev.com/c/6796139 (140.0.7327.0).
    '140.0.7317.0',
    '140.0.7317.1',
    '140.0.7318.0',
    '140.0.7318.1',
    '140.0.7319.0',
    '140.0.7319.1',
    '140.0.7320.0',
    '140.0.7320.1',
    '140.0.7321.0',
    '140.0.7321.1',
    '140.0.7322.0',
    '140.0.7322.1',
    '140.0.7323.0',
    '140.0.7323.1',
    '140.0.7324.0',
    '140.0.7324.1',
    '140.0.7324.2',
    '140.0.7325.0',
    '140.0.7325.1',
    '140.0.7325.2',
    '140.0.7326.0',
    '140.0.7326.1',
    '140.0.7326.2',
    # This specific version contains https://crrev.com/c/6868589, which
    # introduced a bug in the build.py clang script, but lacks
    # https://crrev.com/c/6872233, which contains the fix for the bug.
    '141.0.7370.0',
    # https://crrev.com/c/6896856 introduced a git revert call on the Rust
    # source code that fails to work correctly. This was fixed in 141.0.7384.0
    # by https://crrev.com/c/6899997.
    '141.0.7382.0',
    '141.0.7383.0',
    # third_party/angle/DEPS pointed to VK-GL-CTS commit 2e03601bd4ee, which
    # was removed upstream after the main branch was force-pushed (which seems
    # to happen occasionally, see ANGLE bug 449156265). Skip some M145
    # revisions that depend on an ANGLE revision that depends on this commit
    # were not built before it disappeared to avoid errors like
    # https://ci.chromium.org/ui/b/8692896401331842305
    # This was fixed in Chromium with the ANGLE roll in commit 4b114ef7d
    # (present in 145.0.7624.0).
    '145.0.7620.2',
    '145.0.7620.3',
    '145.0.7623.0',
]

# NaCl support was removed from the Linux builds in
# https://crrev.com/c/4938760, which switched to only checking out the
# NaCl repository on ChromeOS.
# Even though the CL got reverted, it was relanded shortly afterward
# so we can stick to the first time it landed as the version to stop
# shipping NaCl packages.
FIRST_RELEASE_WITHOUT_NACL = '121.0.6110.0'


def version_ships_nacl(version):
  return [int(x) for x in version.split('.')
         ] < [int(x) for x in FIRST_RELEASE_WITHOUT_NACL.split('.')]


def gsutil_upload(api, source, bucket, dest, args):
  api.gsutil.upload(source, bucket, dest, args, name=str('upload ' + dest))


def published_full_tarball(version, ls_result):
  return 'chromium-%s.tar.xz' % version in ls_result


def published_lite_tarball(version, ls_result):
  return 'chromium-%s-lite.tar.xz' % version in ls_result


def published_test_tarball(version, ls_result):
  return 'chromium-%s-testdata.tar.xz' % version in ls_result


def published_nacl_tarball(version, ls_result):
  return 'chromium-%s-nacl.tar.xz' % version in ls_result


def published_all_tarballs(version, ls_result):
  checks = [
      published_full_tarball, published_lite_tarball, published_test_tarball
  ]
  if version_ships_nacl(version):
    checks.append(published_nacl_tarball)
  return all((check(version, ls_result) for check in checks))


def export_tarball(api, args, tarball_name, step_name_suffix):
  try:
    temp_dir = api.path.mkdtemp('export_tarball')
    with api.context(cwd=temp_dir):
      api.step(
          'export_tarball for %s' % step_name_suffix,
          ['python3', api.chromium.resource('export_tarball.py')] + args)
    gsutil_upload(
        api,
        api.path.join(temp_dir, tarball_name),
        'chromium-browser-official',
        tarball_name,
        args=['-a', 'public-read'])

    hashes_result = api.step(
        'generate_hashes for %s' % step_name_suffix,
        [
            'python3',
            api.chromium.resource('generate_hashes.py'),
            api.path.join(temp_dir, tarball_name),
            api.raw_io.output(),
        ],
        step_test_data=lambda: api.raw_io.test_api.output(
            'md5  164ebd6889588da166a52ca0d57b9004  bash'),
    )
    gsutil_upload(
        api,
        api.raw_io.input(hashes_result.raw_io.output),
        'chromium-browser-official',
        tarball_name + '.hashes',
        args=['-a', 'public-read'])
  finally:
    api.file.rmtree('rmtree temp dir', temp_dir)


@contextlib.contextmanager
def copytree_checkout(api, source_dir):
  try:
    temp_dir = api.path.mkdtemp('tmp')
    dest_dir = api.path.join(temp_dir, 'src')
    api.file.copytree('copytree', source_dir, dest_dir, symlinks=True)
    yield dest_dir
  finally:
    api.file.rmtree('rmtree temp dir', temp_dir)


def export_lite_tarball(api, source_dir, version):
  # Make destructive file operations on the copy of the checkout.
  with copytree_checkout(api, source_dir) as dest_dir:
    prune_directories = [
        'android_webview',
        'chrome/android',
        'chromecast',
        'ios',
        'third_party/android_platform',
        'third_party/closure_compiler',
        'third_party/instrumented_libs',
        'third_party/libphonenumber/dist/resources/metadata',
    ]

    if version_ships_nacl(version):
      prune_directories.extend([
          'native_client',
          'native_client_sdk',
      ])

    # These directories will be deleted completely rather than pruned.
    # Only add items that do not ship to end users! We need to retain
    # licensing info for anything that does.
    purge_directories = [
        'build/linux/debian_bullseye_amd64-sysroot',
        'build/linux/debian_bullseye_i386-sysroot',
        'buildtools/reclient',
        'third_party/angle/third_party/VK-GL-CTS',
        'third_party/apache-linux',
        'third_party/catapult/third_party/vinn/third_party/v8',
        'third_party/dawn/third_party/khronos/OpenGL-Registry/specs',
        'third_party/dawn/tools/golang',
        'third_party/jetstream',
        'third_party/llvm',
        'third_party/llvm-build',
        'third_party/llvm-build-tools',
        'third_party/node/linux',
        'third_party/rust-src',
        'third_party/rust-toolchain',
        'third_party/speedometer',
        'third_party/webgl',
        'tools/skia_goldctl',
    ]

    for directory in prune_directories:
      try:
        api.step('prune %s' % directory, [
            'find', api.path.join(dest_dir, directory),
            '-type', 'f,l',
            '-regextype', 'egrep',
            '!', '-regex', '.*\.(gn|gni|grd|grdp|isolate|pydeps)(\.[^ /]+)?',
            '!', '(', '(', '-iname', '*COPYING*',   '-o',
                           '-iname', '*Copyright*', '-o',
                           '-iname', '*LICENSE*',
                      ')',
                      # Files with these extensions are probably not
                      # actual license text.
                      '!', '-iregex', '.*\.(cc|cfg|cpp|h|java|js|json|m|patch|pl|py|rs|sh|sha1|stderr|ts|ya?ml)',
                 ')',
            '-delete'
        ])  # yapf: disable
      except api.step.StepFailure:  # pragma: no cover
        # Ignore failures to delete these directories - they can be inspected
        # later to see whether they have moved to a different location
        # or deleted in different versions of the codebase.
        pass

    for directory in purge_directories:
      api.file.rmtree('purge %s' % directory,
                      api.path.join(dest_dir, directory))

    # Empty directories take up space in the tarball.
    api.step('delete empty directories',
             ['find', dest_dir, '-depth', '-type', 'd', '-empty', '-delete'])

    export_tarball(
        api,
        # Verbose output helps avoid a buildbot timeout when no output
        # is produced for a long time.
        [
            '--remove-nonessential-files', '--basename',
            'chromium-%s' % version,
            'chromium-%s-lite' % version, '--verbose', '--progress',
            '--version', version, '--src-dir', dest_dir
        ],
        'chromium-%s-lite.tar.xz' % version,
        'lite')


def export_nacl_tarball(api, source_dir, version):
  # Make destructive file operations on the copy of the checkout.
  with copytree_checkout(api, source_dir) as dest_dir:
    # Based on instructions from https://sites.google.com/a/chromium.org/dev/
    # nativeclient/pnacl/building-pnacl-components-for-distribution-packagers
    api.step('download pnacl toolchain dependencies', [
        'python3',
        api.path.join(dest_dir, 'native_client', 'toolchain_build',
                      'toolchain_build_pnacl.py'),
        '--verbose',
        '--sync',
        '--sync-only',
        '--disable-git-cache',
    ])

    export_tarball(
        api,
        # Verbose output helps avoid a buildbot timeout when no output
        # is produced for a long time.
        [
            '--remove-nonessential-files', '--basename',
            'chromium-%s' % version,
            'chromium-%s-nacl' % version, '--verbose', '--progress',
            '--version', version, '--src-dir', dest_dir
        ],
        'chromium-%s-nacl.tar.xz' % version,
        'nacl')


def fetch_pgo_profiles(api, source_dir):
  cmd = [
      'python3',
      source_dir.joinpath('tools', 'update_pgo_profiles.py'),
      '--target=linux',
      'update',
      '--gs-url-base=chromium-optimization-profiles/pgo_profiles',
  ]
  api.step('fetch Linux PGO profiles', cmd)


def trigger_publish_tarball_jobs(api):
  ls_result = api.gsutil(
      ['ls', 'gs://chromium-browser-official/'],
      stdout=api.raw_io.output_text(add_output_log=True)).stdout
  missing_releases = set()
  # TODO(phajdan.jr): find better solution than hardcoding version number.
  # We do that currently (carryover from a solution this recipe is replacing)
  # to avoid running into errors with older releases.
  URL = 'https://versionhistory.googleapis.com/v1/chrome/platforms/all/channels/all/versions/all/releases?filter=version>103'
  TEST_DATA = """{ "releases": [
    { "name": "chrome/platforms/ios/channels/canary/versions/103.0.5060.114/releases/1234567890" },
    { "name": "chrome/platforms/mac/channels/canary/versions/103.0.5060.114/releases/1234567890" },
    { "name": "chrome/platforms/win64/channels/canary_asan/versions/103.0.5060.114/releases/1234567890" }
  ]}"""
  text = api.url.get_text(URL, default_test_data=TEST_DATA)
  for release in json.loads(text.output)['releases']:
    name = release['name'].split('/')
    platform, channel, version = name[2], name[4], name[6]
    # Exclude ios - it often uses internal buildspecs so public ones don't work.
    if platform == 'ios':
      continue
    if channel not in ('stable', 'beta', 'dev', 'canary'):
      continue
    if not published_all_tarballs(version, ls_result):
      missing_releases.add(version)
  if not missing_releases:
    api.step.empty('no new releases need publishing')
  else:
    step_result = api.step.empty('%d new releases need publishing' %
                                 len(missing_releases))
    step_result.presentation.logs['missing release'] = '\n'.join(
        missing_releases)

  for version in missing_releases:
    if version not in DENYLISTED_VERSIONS:
      api.scheduler.emit_trigger(
          api.scheduler.BuildbucketTrigger(properties={'version': version}),
          project='infra',
          jobs=['publish_tarball'],
          step_name='trigger publish_tarball for %s' % version)


def publish_tarball(api):
  version = api.properties['version']

  # Although trigger_publish_tarball_jobs() filters versions in
  # DENYLISTED_VERSIONS, it may have already triggered multiple jobs before a
  # version is added to the list. The check here prevents these jobs that will
  # inevitably fail from being run in the first place.
  if version in DENYLISTED_VERSIONS:
    api.step.empty(f'Version {version} is in DENYLISTED_VERSIONS. Skipping.')
    return

  ls_result = api.gsutil(
      ['ls', 'gs://chromium-browser-official/'],
      stdout=api.raw_io.output_text(add_output_log=True)).stdout

  if published_all_tarballs(version, ls_result):
    return

  api.gclient.set_config('chromium')
  solution = api.gclient.c.solutions[0]
  solution.revision = 'refs/tags/%s' % version
  update_result = api.bot_update.ensure_checkout(
      with_branch_heads=True, with_tags=True, suffix=version)
  source_dir = update_result.source_root.path

  api.git('clean', '-dffx')
  with api.context(cwd=source_dir):
    api.gclient(
        'sync',
        ['sync', '-D', '--nohooks', '--with_branch_heads', '--with_tags'])

  api.step('touch chrome/test/data/webui/i18n_process_css_test.html', [
      'touch',
      source_dir.joinpath('chrome', 'test', 'data', 'webui',
                          'i18n_process_css_test.html')
  ])

  # These files are generated in the order specified in Chromium's DEPS file.
  lastchange_path = source_dir.joinpath('build', 'util', 'lastchange.py')
  api.step('Generate LASTCHANGE', [
      'python3',
      lastchange_path,
      '-o',
      source_dir.joinpath('build', 'util', 'LASTCHANGE'),
  ])
  api.step('Generate gpu/config/gpu_lists_version.h', [
      'python3',
      lastchange_path,
      '-m',
      'GPU_LISTS_VERSION',
      '--revision-id-only',
      '--header',
      source_dir.joinpath('gpu', 'config', 'gpu_lists_version.h'),
  ])
  api.step('Generate skia/ext/skia_commit_hash.h', [
      'python3',
      lastchange_path,
      '-s',
      source_dir.joinpath('third_party', 'skia'),
      '-m',
      'SKIA_COMMIT_HASH',
      '--header',
      source_dir.joinpath('skia', 'ext', 'skia_commit_hash.h'),
  ])
  # The --revision option was introduced in 105.0.5148.2, so we need to skip
  # this call when building earlier versions.
  if [int(x) for x in version.split('.')] >= [105, 0, 5148, 2]:
    api.step('Generate gpu/webgpu/DAWN_VERSION and dawn_commit_hash.h', [
        'python3',
        lastchange_path,
        '-s',
        source_dir.joinpath('third_party', 'dawn'),
        '-m',
        'DAWN_COMMIT_HASH',
        '--revision',
        source_dir.joinpath('gpu', 'webgpu', 'DAWN_VERSION'),
        '--header',
        source_dir.joinpath('gpu', 'webgpu', 'dawn_commit_hash.h'),
    ])
  if int(version.split('.')[0]) >= 145:
    with api.context(
        cwd=source_dir.joinpath('third_party', 'devtools-frontend', 'src')):
      api.step('Sync Rollup native libraries to node_modules', [
          'python3',
          api.path.join('scripts', 'deps', 'sync_rollup_libs.py'),
      ])

  api.file.copy('copy clang-format', api.chromium.resource('clang-format'),
                source_dir.joinpath('buildtools', 'linux64', 'clang-format'))

  update_script = 'build.py'
  update_args = [
      '--without-android', '--use-system-cmake', '--skip-build',
      '--without-fuchsia'
  ]
  # This argument was removed in https://crrev.com/c/4702649
  # See https://bugs.chromium.org/p/chromium/issues/detail?id=1459650#c17
  if [int(x) for x in version.split('.')] < [117, 0, 5917, 0]:
    update_args.append('--gcc-toolchain=/usr')
  # Explicitly passing python3 will not be necessary once
  # https://chromium-review.googlesource.com/c/chromium/src/+/3253157
  # is in all release channels.
  api.step('download clang sources', [
      'python3',
      source_dir.joinpath('tools', 'clang', 'scripts', update_script)
  ] + update_args)

  fetch_pgo_profiles(api, source_dir)

  # This was originally enabled for all versions in https://crrev.com/c/4658882
  # but failed on M115. It is safe to assume that this only works as expected
  # in the Publish Tarball bot from M117 on.
  # (Rust support was officially enabled on Linux in M116, but enabling it here
  # from M117 on should be fine per https://crrev.com/c/4681323)
  if int(version.split('.')[0]) >= 117:
    build_rust_script = 'build_rust.py'
    build_rust_args = ['--sync-for-gnrt']
    api.step(
        'download rustc sources',
        ['python3',
         source_dir.joinpath('tools', 'rust', build_rust_script)] +
        build_rust_args)

  # https://chromium.googlesource.com/chromium/src/+/065d83e42bb327e81b045fd04c37eef2934be298
  if [int(x) for x in version.split('.')] >= [113, 0, 5656, 0]:
    api.step('Fetch V8 PGO profiles', [
        'python3',
        source_dir.joinpath('v8', 'tools', 'builtins-pgo',
                            'download_profiles.py'),
        'download',
        '--depot-tools',
        source_dir.joinpath('third_party', 'depot_tools'),
    ])

  # The structure of the archive has been changed because of the GCS first class
  # migration in https://crrev.com/c/5564966
  # The archive will be downloaded and extracted in gclient sync and no longer
  # use download_from_google_storage.py.
  if [int(x) for x in version.split('.')] < [128, 0, 6534, 0]:
    node_modules_sha_path = source_dir.joinpath('third_party', 'node',
                                                'node_modules.tar.gz.sha1')
    if api.path.exists(node_modules_sha_path):
      api.step('webui_node_modules', [
          'python3',
          api.depot_tools.download_from_google_storage_path,
          '--no_resume',
          '--extract',
          '--no_auth',
          '--bucket',
          'chromium-nodejs',
          '-s',
          node_modules_sha_path,
      ])

  try:
    temp_dir = api.path.mkdtemp('gn')
    git_root = temp_dir / 'gn'
    api.step('checkout gn',
             ['git', 'clone', 'https://gn.googlesource.com/gn', git_root])

    # Check out the same version of gn as the one pulled down from gclient.
    result = api.step(
        'get gn version',
        [source_dir.joinpath('buildtools', 'linux64', 'gn'), '--version'],
        stdout=api.raw_io.output_text())
    match = re.match(r'\d+ \((.+)\)$', result.stdout.strip())
    commit = match.group(1)
    api.step('checkout gn commit', ['git', '-C', git_root, 'checkout', commit])

    tools_gn = source_dir.joinpath('tools', 'gn')
    api.step('generate last_commit_position.h',
             ['python3', git_root.joinpath('build', 'gen.py')])
    api.file.remove('rm README.md', tools_gn / 'README.md')
    for f in api.file.listdir(
        'listdir gn', git_root, test_data=['build', '.git']):
      basename = api.path.basename(f)
      if basename not in ['.git', '.gitignore', '.linux-sysroot', 'out']:
        api.file.move('move gn ' + basename, f, tools_gn / basename)
    api.file.move('move last_commit_position.h',
                  git_root.joinpath('out', 'last_commit_position.h'),
                  tools_gn / 'bootstrap' / 'last_commit_position.h')
  finally:
    api.file.rmtree('rmtree temp dir', temp_dir)

  with api.defer.context() as defer:
    if not published_full_tarball(version, ls_result):
      defer(
          export_tarball,
          api,
          # Verbose output helps avoid a buildbot timeout when no output
          # is produced for a long time.
          [
              '--remove-nonessential-files',
              'chromium-%s' % version, '--verbose', '--progress', '--version',
              version, '--src-dir', source_dir
          ],
          'chromium-%s.tar.xz' % version,
          'full')

      # Trigger a tarball build now that the full tarball has been uploaded.
      defer(
          api.scheduler.emit_trigger,
          api.scheduler.BuildbucketTrigger(properties={'version': version}),
          project='infra',
          jobs=['Build From Tarball'],
      )

    if not published_test_tarball(version, ls_result):
      defer(
          export_tarball,
          api,
          # Verbose output helps avoid a buildbot timeout when no output
          # is produced for a long time.
          [
              '--test-data', '--basename',
              'chromium-%s' % version,
              'chromium-%s-testdata' % version, '--verbose', '--progress',
              '--version', version, '--src-dir', source_dir
          ],
          'chromium-%s-testdata.tar.xz' % version,
          'testdata')

    if not published_lite_tarball(version, ls_result):
      defer(export_lite_tarball, api, source_dir, version)

    if version_ships_nacl(version) and not published_nacl_tarball(
        version, ls_result):
      defer(export_nacl_tarball, api, source_dir, version)


def RunSteps(api):
  if 'version' not in api.properties:
    # This code path executes on 'publish_tarball_dispatcher' builder.
    trigger_publish_tarball_jobs(api)
  else:
    # This code path executes on 'publish_tarball' builder.
    publish_tarball(api)


def GenTests(api):
  yield (
      api.test('basic') + api.buildbucket.generic_build() +
      api.properties(version='128.0.6534.0') + api.platform('linux', 64) +
      api.step_data('gsutil ls', stdout=api.raw_io.output_text('')) +
      api.step_data(
          'get gn version', stdout=api.raw_io.output_text('1496 (0790d304)')))

  yield (api.test('basic-without-nacl-with-nodejs') +
         api.buildbucket.generic_build() +
         api.properties(version='121.0.6110.0') + api.platform('linux', 64) +
         api.step_data('gsutil ls', stdout=api.raw_io.output_text('')) +
         api.step_data(
             'get gn version', stdout=api.raw_io.output_text('1496 (0790d304)'))
         + api.path.exists(
             api.path.start_dir.joinpath('src', 'third_party', 'node',
                                         'node_modules.tar.gz.sha1')) +
         api.post_process(post_process.MustRun, 'webui_node_modules'))

  yield (
      api.test('basic-with-nacl') + api.buildbucket.generic_build() +
      api.properties(version='117.0.5884.0') + api.platform('linux', 64) +
      api.step_data('gsutil ls', stdout=api.raw_io.output_text('')) +
      api.step_data(
          'get gn version', stdout=api.raw_io.output_text('1496 (0790d304)')) +
      api.path.exists(
          api.path.start_dir.joinpath('src', 'third_party', 'node',
                                      'node_modules.tar.gz.sha1')))

  yield (
      api.test('basic-with-gcc-toolchain-arg') +
      api.buildbucket.generic_build() + api.properties(version='117.0.5884.0') +
      api.platform('linux', 64) +
      api.step_data('gsutil ls', stdout=api.raw_io.output_text('')) +
      api.step_data(
          'get gn version', stdout=api.raw_io.output_text('1496 (0790d304)')) +
      api.path.exists(
          api.path.start_dir.joinpath('src', 'third_party', 'node',
                                      'node_modules.tar.gz.sha1')))

  yield (
      api.test('basic-no-dawn-version') + api.buildbucket.generic_build() +
      api.properties(version='103.0.4273.0') + api.platform('linux', 64) +
      api.step_data('gsutil ls', stdout=api.raw_io.output_text('')) +
      api.step_data(
          'get gn version', stdout=api.raw_io.output_text('1496 (0790d304)')) +
      api.path.exists(
          api.path.start_dir.joinpath('src', 'third_party', 'node',
                                      'node_modules.tar.gz.sha1')))

  yield (
      api.test('basic-with-native-rollup') + api.buildbucket.generic_build() +
      api.properties(version='145.0.7632.38') + api.platform('linux', 64) +
      api.step_data(
          'get gn version', stdout=api.raw_io.output_text('1496 (0790d304)')) +
      api.path.exists(
          api.path.start_dir.joinpath(
              'src', 'third_party', 'devtools-frontend', 'src', 'node_modules',
              '@rollup', 'rollup-linux-x64-gnu', 'rollup-linux-x64-gnu.node')))

  yield (api.test('dupe') + api.buildbucket.generic_build() + api.properties(
      version='103.0.5060.114'
  ) + api.platform('linux', 64) + api.step_data(
      'gsutil ls',
      stdout=api.raw_io.output_text(
          'gs://chromium-browser-official/chromium-103.0.5060.114.tar.xz\n'
          'gs://chromium-browser-official/chromium-103.0.5060.114-lite.tar.xz\n'
          'gs://chromium-browser-official/'
          'chromium-103.0.5060.114-testdata.tar.xz\n'
          'gs://chromium-browser-official/chromium-103.0.5060.114-nacl.tar.xz\n'
      )))

  yield (api.test('triggered-in-denylisted_versions') +
         api.buildbucket.generic_build() +
         api.properties(version='140.0.7325.0') + api.platform('linux', 64))

  yield (api.test('trigger') + api.buildbucket.generic_build() +
         api.platform('linux', 64) +
         api.step_data('gsutil ls', stdout=api.raw_io.output_text('')))

  yield api.test(
      'trigger_noop',
      api.buildbucket.generic_build(),
      api.platform('linux', 64),
      api.url.text(
          'GET https://versionhistory.googleapis.com/v1/chrome/platforms/all/channels/all/versions/all/releases?filter=version>103',
          """{ "releases": [
            { "name": "chrome/platforms/linux/channels/canary/versions/104.0.5112.79/releases/1234567890" }
          ]}"""),
      api.step_data(
          'gsutil ls',
          stdout=api.raw_io.output_text(
              'gs://chromium-browser-official/chromium-104.0.5112.79.tar.xz\n'
              'gs://chromium-browser-official/chromium-104.0.5112.79-lite.tar.xz\n'
              'gs://chromium-browser-official/chromium-104.0.5112.79-testdata.tar.xz\n'
              'gs://chromium-browser-official/chromium-104.0.5112.79-nacl.tar.xz\n'
          )),
      api.post_process(post_process.MustRun, 'no new releases need publishing'),
      api.post_process(post_process.DropExpectation),
  )
