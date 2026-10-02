# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

# pylint: disable=line-too-long

from recipe_engine import post_process, recipe_api

import contextlib
import json
import re

from dataclasses import dataclass

from recipe_engine.recipe_api import RecipeScriptApi
from recipe_engine.recipe_test_api import RecipeTestApi

from RECIPE_MODULES.build import chromium
from RECIPE_MODULES.depot_tools import (
  bot_update,
  depot_tools,
  gclient,
  git,
  gsutil,
)
from RECIPE_MODULES.recipe_engine import (
  buildbucket,
  context,
  defer,
  file,
  path,
  platform,
  properties,
  raw_io,
  scheduler,
  step,
  url,
)


@dataclass
class DEPS(RecipeScriptApi):
  bot_update: bot_update.API
  buildbucket: buildbucket.API
  chromium: chromium.API
  context: context.API
  defer: defer.API
  depot_tools: depot_tools.API
  file: file.API
  gclient: gclient.API
  git: git.API
  gsutil: gsutil.API
  path: path.API
  platform: platform.API
  properties: properties.API
  raw_io: raw_io.API
  scheduler: scheduler.API
  step: step.API
  url: url.API


@dataclass
class TEST_DEPS(RecipeTestApi):
  bot_update: bot_update.TEST_API
  buildbucket: buildbucket.TEST_API
  chromium: chromium.TEST_API
  context: context.TEST_API
  file: file.TEST_API
  gclient: gclient.TEST_API
  git: git.TEST_API
  path: path.TEST_API
  platform: platform.TEST_API
  properties: properties.TEST_API
  raw_io: raw_io.TEST_API
  scheduler: scheduler.TEST_API
  step: step.TEST_API
  url: url.TEST_API


# The minimum version that this script will try to publish a tarball for. Older
# versions may fail due to a number of reasons (including infra ones) and are
# of no relevance to anyone building a recent Chromium version at this point.
MINIMUM_VERSION_FOR_TARBALLS = '145.0.7624.0'


# Sometimes a revision will be bad because the checkout will fail, causing
# publish_tarball to fail.  The version will stay in the version list for
# several months and publish_tarball will keep re-running on the same broken
# version.  This denylist exists to exclude those broken versions so the bot
# doesn't keep retrying and sending build failure emails out.
#
# Note: MINIMUM_VERSION_FOR_TARBALLS is the minimum version that this script
# will attempt to build a tarball for, so entries in this list should be >=
# MINIMUM_VERSION_FOR_TARBALLS.
DENYLISTED_VERSIONS = []


def should_skip_tarball_for_version(version):
  # Anything older than MINIMUM_VERSION_FOR_TARBALLS is skipped.
  # Any version in DENYLISTED_VERSIONS is skipped.
  return (
    [int(x) for x in version.split('.')]
    < [int(x) for x in MINIMUM_VERSION_FOR_TARBALLS.split('.')]
  ) or version in DENYLISTED_VERSIONS


def gsutil_upload(api: DEPS, source, bucket, dest, args):
  api.gsutil.upload(source, bucket, dest, args, name=str('upload ' + dest))


def published_full_tarball(version, ls_result):
  return 'chromium-%s.tar.xz' % version in ls_result


def published_lite_tarball(version, ls_result):
  return 'chromium-%s-lite.tar.xz' % version in ls_result


def published_test_tarball(version, ls_result):
  return 'chromium-%s-testdata.tar.xz' % version in ls_result


def published_all_tarballs(version, ls_result):
  checks = [
    published_full_tarball,
    published_lite_tarball,
    published_test_tarball,
  ]
  return all((check(version, ls_result) for check in checks))


def export_tarball(api: DEPS, args, tarball_name, step_name_suffix):
  try:
    temp_dir = api.path.mkdtemp('export_tarball')
    with api.context(cwd=temp_dir):
      api.step(
        'export_tarball for %s' % step_name_suffix,
        ['python3', api.chromium.resource('export_tarball.py')] + args,
      )
    gsutil_upload(
      api,
      api.path.join(temp_dir, tarball_name),
      'chromium-browser-official',
      tarball_name,
      args=['-a', 'public-read'],
    )

    hashes_result = api.step(
      'generate_hashes for %s' % step_name_suffix,
      [
        'python3',
        api.chromium.resource('generate_hashes.py'),
        api.path.join(temp_dir, tarball_name),
        api.raw_io.output(),
      ],
      step_test_data=lambda: api.raw_io.test_api.output(
        'md5  164ebd6889588da166a52ca0d57b9004  bash'
      ),
    )
    gsutil_upload(
      api,
      api.raw_io.input(hashes_result.raw_io.output),
      'chromium-browser-official',
      tarball_name + '.hashes',
      args=['-a', 'public-read'],
    )
  finally:
    api.file.rmtree('rmtree temp dir', temp_dir)


@contextlib.contextmanager
def copytree_checkout(api: DEPS, source_dir):
  try:
    temp_dir = api.path.mkdtemp('tmp')
    dest_dir = api.path.join(temp_dir, 'src')
    api.file.copytree('copytree', source_dir, dest_dir, symlinks=True)
    yield dest_dir
  finally:
    api.file.rmtree('rmtree temp dir', temp_dir)


def export_lite_tarball(api: DEPS, source_dir, version):
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
      api.file.rmtree(
        'purge %s' % directory, api.path.join(dest_dir, directory)
      )

    # Empty directories take up space in the tarball.
    api.step(
      'delete empty directories',
      ['find', dest_dir, '-depth', '-type', 'd', '-empty', '-delete'],
    )

    export_tarball(
      api,
      # Verbose output helps avoid a buildbot timeout when no output
      # is produced for a long time.
      [
        '--remove-nonessential-files',
        '--basename',
        'chromium-%s' % version,
        'chromium-%s-lite' % version,
        '--verbose',
        '--progress',
        '--version',
        version,
        '--src-dir',
        dest_dir,
      ],
      'chromium-%s-lite.tar.xz' % version,
      'lite',
    )


def fetch_pgo_profiles(api: DEPS, source_dir):
  cmd = [
    'python3',
    source_dir.joinpath('tools', 'update_pgo_profiles.py'),
    '--target=linux',
    'update',
    '--gs-url-base=chromium-optimization-profiles/pgo_profiles',
  ]
  api.step('fetch Linux PGO profiles', cmd)


def trigger_publish_tarball_jobs(api: DEPS):
  ls_result = api.gsutil(
    ['ls', 'gs://chromium-browser-official/'],
    stdout=api.raw_io.output_text(add_output_log=True),
  ).stdout
  missing_releases = set()
  # TODO(phajdan.jr): find better solution than hardcoding version number.
  # We do that currently (carryover from a solution this recipe is replacing)
  # to avoid running into errors with older releases.
  URL = 'https://versionhistory.googleapis.com/v1/chrome/platforms/all/channels/all/versions/all/releases?filter=version>145'
  TEST_DATA = """{ "releases": [
    { "name": "chrome/platforms/ios/channels/canary/versions/148.0.7752.0/releases/1234567890" },
    { "name": "chrome/platforms/mac/channels/canary/versions/148.0.7752.0/releases/1234567890" },
    { "name": "chrome/platforms/win64/channels/canary_asan/versions/148.0.7752.0/releases/1234567890" }
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
    step_result = api.step.empty(
      '%d new releases need publishing' % len(missing_releases)
    )
    step_result.presentation.logs['missing release'] = '\n'.join(
      missing_releases
    )

  for version in missing_releases:
    if not should_skip_tarball_for_version(version):
      api.scheduler.emit_trigger(
        api.scheduler.BuildbucketTrigger(properties={'version': version}),
        project='infra',
        jobs=['publish_tarball'],
        step_name='trigger publish_tarball for %s' % version,
      )


def publish_tarball(api: DEPS):
  version = api.properties['version']

  # Although trigger_publish_tarball_jobs() filters versions in
  # DENYLISTED_VERSIONS, it may have already triggered multiple jobs before a
  # version is added to the list. The check here prevents these jobs that will
  # inevitably fail from being run in the first place.
  if should_skip_tarball_for_version(version):
    api.step.empty(
      f'Version {version} is in DENYLISTED_VERSIONS (or is older than '
      f'{MINIMUM_VERSION_FOR_TARBALLS}). Skipping.'
    )
    return

  ls_result = api.gsutil(
    ['ls', 'gs://chromium-browser-official/'],
    stdout=api.raw_io.output_text(add_output_log=True),
  ).stdout

  if published_all_tarballs(version, ls_result):
    return

  api.gclient.set_config('chromium')
  solution = api.gclient.c.solutions[0]
  solution.revision = 'refs/tags/%s' % version
  update_result = api.bot_update.ensure_checkout(
    with_branch_heads=True, with_tags=True, suffix=version, patch=False
  )
  source_dir = update_result.source_root.path

  api.git('clean', '-dffx')
  with api.context(cwd=source_dir):
    api.gclient('sync', ['sync', '-D', '--no-history'])

  api.step(
    'touch chrome/test/data/webui/i18n_process_css_test.html',
    [
      'touch',
      source_dir.joinpath(
        'chrome', 'test', 'data', 'webui', 'i18n_process_css_test.html'
      ),
    ],
  )

  api.file.copy(
    'copy clang-format',
    api.chromium.resource('clang-format'),
    source_dir.joinpath('buildtools', 'linux64', 'clang-format'),
  )

  update_script = 'build.py'
  update_args = [
    '--without-android',
    '--use-system-cmake',
    '--skip-build',
    '--without-fuchsia',
  ]
  # Explicitly passing python3 will not be necessary once
  # https://chromium-review.googlesource.com/c/chromium/src/+/3253157
  # is in all release channels.
  api.step(
    'download clang sources',
    ['python3', source_dir.joinpath('tools', 'clang', 'scripts', update_script)]
    + update_args,
  )

  fetch_pgo_profiles(api, source_dir)

  build_rust_script = 'build_rust.py'
  build_rust_args = ['--sync-for-gnrt']
  api.step(
    'download rustc sources',
    ['python3', source_dir.joinpath('tools', 'rust', build_rust_script)]
    + build_rust_args,
  )

  api.step(
    'Fetch V8 PGO profiles',
    [
      'python3',
      source_dir.joinpath(
        'v8', 'tools', 'builtins-pgo', 'download_profiles.py'
      ),
      'download',
      '--depot-tools',
      source_dir.joinpath('third_party', 'depot_tools'),
    ],
  )

  try:
    temp_dir = api.path.mkdtemp('gn')
    git_root = temp_dir / 'gn'
    api.step(
      'checkout gn',
      ['git', 'clone', 'https://gn.googlesource.com/gn', git_root],
    )

    # Check out the same version of gn as the one pulled down from gclient.
    result = api.step(
      'get gn version',
      [source_dir.joinpath('buildtools', 'linux64', 'gn'), '--version'],
      stdout=api.raw_io.output_text(),
    )
    match = re.match(r'\d+ \((.+)\)$', result.stdout.strip())
    commit = match.group(1)
    api.step('checkout gn commit', ['git', '-C', git_root, 'checkout', commit])

    tools_gn = source_dir.joinpath('tools', 'gn')
    api.step(
      'generate last_commit_position.h',
      ['python3', git_root.joinpath('build', 'gen.py')],
    )
    api.file.remove('rm README.md', tools_gn / 'README.md')
    for f in api.file.listdir(
      'listdir gn', git_root, test_data=['build', '.git']
    ):
      basename = api.path.basename(f)
      if basename not in ['.git', '.gitignore', '.linux-sysroot', 'out']:
        api.file.move('move gn ' + basename, f, tools_gn / basename)
    api.file.move(
      'move last_commit_position.h',
      git_root.joinpath('out', 'last_commit_position.h'),
      tools_gn / 'bootstrap' / 'last_commit_position.h',
    )
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
          'chromium-%s' % version,
          '--verbose',
          '--progress',
          '--version',
          version,
          '--src-dir',
          source_dir,
        ],
        'chromium-%s.tar.xz' % version,
        'full',
      )

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
          '--test-data',
          '--basename',
          'chromium-%s' % version,
          'chromium-%s-testdata' % version,
          '--verbose',
          '--progress',
          '--version',
          version,
          '--src-dir',
          source_dir,
        ],
        'chromium-%s-testdata.tar.xz' % version,
        'testdata',
      )

    if not published_lite_tarball(version, ls_result):
      defer(export_lite_tarball, api, source_dir, version)


def RunSteps(api: DEPS):
  if 'version' not in api.properties:
    # This code path executes on 'publish_tarball_dispatcher' builder.
    trigger_publish_tarball_jobs(api)
  else:
    # This code path executes on 'publish_tarball' builder.
    publish_tarball(api)


def GenTests(api: TEST_DEPS):
  yield (
    api.test('basic')
    + api.buildbucket.generic_build()
    + api.properties(version='148.0.7752.0')
    + api.platform('linux', 64)
    + api.step_data('gsutil ls', stdout=api.raw_io.output_text(''))
    + api.step_data(
      'get gn version', stdout=api.raw_io.output_text('1496 (0790d304)')
    )
  )

  yield (
    api.test('basic-with-native-rollup')
    + api.buildbucket.generic_build()
    + api.properties(version='145.0.7632.38')
    + api.platform('linux', 64)
    + api.step_data(
      'get gn version', stdout=api.raw_io.output_text('1496 (0790d304)')
    )
    + api.path.exists(
      api.path.start_dir.joinpath(
        'src',
        'third_party',
        'devtools-frontend',
        'src',
        'node_modules',
        '@rollup',
        'rollup-linux-x64-gnu',
        'rollup-linux-x64-gnu.node',
      )
    )
  )

  yield (
    api.test('dupe')
    + api.buildbucket.generic_build()
    + api.properties(version='148.0.7752.0')
    + api.platform('linux', 64)
    + api.step_data(
      'gsutil ls',
      stdout=api.raw_io.output_text(
        'gs://chromium-browser-official/chromium-148.0.7752.0.tar.xz\n'
        'gs://chromium-browser-official/chromium-148.0.7752.0-lite.tar.xz\n'
        'gs://chromium-browser-official/'
        'chromium-148.0.7752.0-testdata.tar.xz\n'
        'gs://chromium-browser-official/chromium-148.0.7752.0-nacl.tar.xz\n'
      ),
    )
  )

  yield (
    api.test('skipped-version')
    + api.buildbucket.generic_build()
    + api.properties(version='140.0.7325.0')
    + api.platform('linux', 64)
  )

  yield (
    api.test('trigger')
    + api.buildbucket.generic_build()
    + api.platform('linux', 64)
    + api.step_data('gsutil ls', stdout=api.raw_io.output_text(''))
  )

  yield api.test(
    'trigger_noop',
    api.buildbucket.generic_build(),
    api.platform('linux', 64),
    api.url.text(
      'GET https://versionhistory.googleapis.com/v1/chrome/platforms/all/channels/all/versions/all/releases?filter=version>145',
      """{ "releases": [
            { "name": "chrome/platforms/linux/channels/canary/versions/148.0.7752.0/releases/1234567890" }
          ]}""",
    ),
    api.step_data(
      'gsutil ls',
      stdout=api.raw_io.output_text(
        'gs://chromium-browser-official/chromium-148.0.7752.0.tar.xz\n'
        'gs://chromium-browser-official/chromium-148.0.7752.0-lite.tar.xz\n'
        'gs://chromium-browser-official/chromium-148.0.7752.0-testdata.tar.xz\n'
        'gs://chromium-browser-official/chromium-148.0.7752.0-nacl.tar.xz\n'
      ),
    ),
    api.post_process(post_process.MustRun, 'no new releases need publishing'),
    api.post_process(post_process.DropExpectation),
  )
